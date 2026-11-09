"""Voice-to-Hardware Simulator backend.

Run from the backend/ folder, inside the venv:
    uvicorn main:app --reload --port 8000

API keys (BYOK): each request may bring the visitor's own keys as headers (X-Groq-Key, X-Gemini-Key,
X-Gnani-Key; Live mode sends the Gnani key in its first WebSocket message). They are used for that
request only and never stored or logged. Without them the keys in .env are used, unless
REQUIRE_USER_KEYS=1 (a public server), where visitors must bring their own. See keys.py.
"""
import asyncio
import itertools
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal, Optional, Union

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")  # before importing modules that read env

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import gnani
import graph as agent
import keys
from compiler import compile_sketch
from llm import LLMError, has_key, provider_info

MAX_CODE_BYTES = 8_000
MAX_AUDIO_BYTES = 2_000_000  # 30 s of 16 kHz mono 16-bit WAV is about 1 MB

app = FastAPI(title="Voice-to-Hardware Simulator")

# Allow the Vite dev server (any localhost port) to call us.
LOCAL_ORIGIN = r"http://(localhost|127\.0\.0\.1)(:\d+)?"
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN,
    allow_methods=["*"],
    allow_headers=["*"],
)

GRAPH = agent.build_graph()
NO_LLM_KEY = "no LLM API key: add a Groq or Gemini key with the 🔑 API keys button"


def origin_allowed(origin, host):
    """Our own page: the Vite dev server on localhost, or a page served by this backend (same host)."""
    if re.fullmatch(LOCAL_ORIGIN, origin or ""):
        return True
    netloc = urlparse(origin or "").netloc
    return bool(netloc) and netloc == host


def gnani_key_or_401(request):
    """The Gnani key for this request (the visitor's, or .env when allowed)."""
    key = keys.get("gnani", keys.from_headers(request.headers).get("gnani"))
    if not key:
        raise HTTPException(status_code=401, detail=keys.missing_message("gnani"))
    return key


class CompileRequest(BaseModel):
    code: str
    board: str = agent.DEFAULT_BOARD


def board_or_400(board_id):
    if board_id not in agent.BOARDS:
        raise HTTPException(status_code=400, detail=f"unknown board '{board_id}' (have: {sorted(agent.BOARDS)})")
    return agent.BOARDS[board_id]


class RunRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=1000)
    circuit: dict
    # "auto": the agent decides between answering (chat) and building; "build" skips that step
    # (e.g. a "Try next" chip); "chat" asks for an answer only.
    mode: Literal["auto", "build", "chat"] = "auto"
    via: Literal["text", "voice"] = "text"
    # Dev switch: "compile" breaks the code before the first compile; "timing" makes it 4x too slow
    # so the measured check fails. true is accepted as "compile" (Phase 2 clients).
    debug_break: Union[bool, Literal["", "compile", "timing"]] = ""
    board: str = agent.DEFAULT_BOARD


@app.get("/health")
def health():
    """Tools and keys on this server. With REQUIRE_USER_KEYS the server's own keys are never used,
    so they are reported as not set and the page asks visitors for theirs."""
    try:
        provider, model = provider_info()
    except LLMError as e:
        provider, model = f"error: {e}", None
    return {
        "ok": True,
        "arduino_cli": shutil.which("arduino-cli"),
        "node": shutil.which("node"),
        "llm_provider": provider,
        "llm_model": model,
        "user_keys_required": keys.user_keys_required(),
        "llm_key_set": has_key(),
        "gnani_key_set": bool(keys.get("gnani")),
    }


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: Optional[str] = None  # kn-IN, hi-IN, en-IN; picked from the script if missing


@app.post("/transcribe")
async def transcribe_route(request: Request):
    """Body: a WAV file (the browser records and encodes it). Returns {transcript, language}."""
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="no audio received")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="recording too long (max about 30 s)")
    key = gnani_key_or_401(request)
    try:
        return gnani.transcribe(audio, key=key)
    except gnani.GnaniError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/speak")
def speak_route(req: SpeakRequest, request: Request):
    """Text in, audio out (Timbre). Only the short spoken summary is sent here, never code."""
    key = gnani_key_or_401(request)
    try:
        audio, content_type = gnani.speak(req.text, req.language, key=key)
    except gnani.GnaniError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=audio, media_type=content_type)


@app.post("/speak/stream")
def speak_stream_route(req: SpeakRequest, request: Request):
    """Live mode: Timbre streaming. The body is raw 16-bit mono PCM at the rate in X-Sample-Rate,
    sent while it is generated. The first chunk is fetched before answering, so a Gnani error
    becomes a proper HTTP error instead of a cut-off stream."""
    chunks = gnani.speak_stream(req.text, req.language, key=gnani_key_or_401(request))
    try:
        first = next(chunks)
    except StopIteration:
        raise HTTPException(status_code=502, detail="text-to-speech returned no audio")
    except gnani.GnaniError as e:
        raise HTTPException(status_code=502, detail=str(e))

    def body():
        try:
            yield from itertools.chain([first], chunks)
        except gnani.GnaniError:
            return  # the audio so far has been sent; the browser plays what it got

    return StreamingResponse(body(), media_type="application/octet-stream",
                             headers={"X-Sample-Rate": str(gnani.TTS_STREAM_RATE), "Cache-Control": "no-cache",
                                      "Access-Control-Expose-Headers": "X-Sample-Rate"})


@app.websocket("/live/stt")
async def live_stt(browser: WebSocket):
    """Live mode: relay the browser's microphone to Prisma streaming and its messages back.
    Browsers cannot set WebSocket headers, so the backend adds the API key to the upstream connection.
    Browser -> us: first {"type":"auth","gnani_key":"..."} (the visitor's key; optional locally, where
    .env is used), then 1,024-byte PCM frames (16 kHz, 16-bit mono, every 32 ms) and the
    {"type":"commit"} control frame. Us -> browser: Prisma's JSON messages (connected, speech_start,
    processing, transcript, error), plus {"type":"error"} if Gnani cannot be reached."""
    if not origin_allowed(browser.headers.get("origin"), browser.headers.get("host")):
        await browser.close(code=1008)  # only our own page may use this relay
        return
    await browser.accept()
    user_key = ""
    try:
        first = await asyncio.wait_for(browser.receive(), timeout=10)
    except asyncio.TimeoutError:
        first = {}
    if first.get("type") == "websocket.disconnect":
        return
    if first.get("text"):
        try:
            data = json.loads(first["text"])
        except ValueError:
            data = {}
        if isinstance(data, dict) and data.get("type") == "auth":
            user_key = keys.clean(data.get("gnani_key"))
    key = keys.get("gnani", user_key)
    if not key:
        await browser.send_json({"type": "error", "message": keys.missing_message("gnani")})
        await browser.close()
        return
    try:
        headers = gnani.stt_stream_headers(key)
        upstream = await websockets.connect(gnani.STT_STREAM_URL, additional_headers=headers, open_timeout=15)
    except (gnani.GnaniError, OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        message = (f"Gnani rejected the API key (HTTP {status}); check your Gnani key" if status in (401, 403)
                   else f"could not start Gnani speech-to-text: {e}")
        await browser.send_json({"type": "error", "message": message})
        await browser.close()
        return

    async def browser_to_gnani():
        while True:
            msg = await browser.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                if len(msg["bytes"]) == 1024:  # Prisma requires exactly 1,024-byte frames
                    await upstream.send(msg["bytes"])
            elif msg.get("text"):
                try:
                    kind = json.loads(msg["text"]).get("type")
                except (ValueError, AttributeError):
                    continue
                if kind == "commit":  # the only control frame we pass on
                    await upstream.send(json.dumps({"type": "commit"}))

    async def gnani_to_browser():
        async for raw in upstream:
            if isinstance(raw, str):
                await browser.send_text(raw)

    tasks = [asyncio.create_task(browser_to_gnani()), asyncio.create_task(gnani_to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:  # a normal close on either side is not worth a log line
            if t.exception() and not isinstance(t.exception(), (websockets.ConnectionClosed, WebSocketDisconnect, RuntimeError)):
                print(f"[live] relay stopped: {t.exception()!r}", flush=True)
    finally:
        await upstream.close()
        try:
            close_code = upstream.close_code or 1000
            await browser.close(code=1000 if close_code in (1000, 1005, 1006) else close_code)
        except RuntimeError:
            pass  # the browser side is already closed


@app.get("/boards")
def boards():
    return [{"id": b["id"], "name": b["name"], "chip": b["chip"]} for b in agent.BOARDS.values()]


@app.get("/session/{session_id}")
def session_route(session_id: str):
    """The conversation, circuit, board and code of a session, so a reloaded page can continue it."""
    if not 1 <= len(session_id) <= 64:
        raise HTTPException(status_code=400, detail="bad session id")
    return agent.session_snapshot(GRAPH, session_id)


@app.post("/compile")
def compile_route(req: CompileRequest):
    if not req.code.strip():
        return {"ok": False, "error": "code is empty"}
    if len(req.code.encode("utf-8")) > MAX_CODE_BYTES:
        return {"ok": False, "error": f"code is over {MAX_CODE_BYTES} bytes"}
    start = time.perf_counter()
    result = compile_sketch(req.code, fqbn=board_or_400(req.board)["fqbn"])
    result["compile_ms"] = round((time.perf_counter() - start) * 1000)
    return result


@app.post("/run")
def run_route(req: RunRequest, request: Request):
    """Streams Server-Sent Events: {"type":"step",...} while working, then {"type":"result",...}."""
    # The user's circuit must be valid before the agent starts.
    problem = agent.check_circuit(req.circuit, board_or_400(req.board))
    if problem:
        raise HTTPException(status_code=422, detail=f"circuit: {problem}")
    user_keys = keys.from_headers(request.headers)  # this request only: never stored or logged
    if not keys.call(user_keys, has_key):
        raise HTTPException(status_code=401,
                            detail=NO_LLM_KEY + ("" if keys.user_keys_required() else " (or set one in .env)"))

    mode = "compile" if req.debug_break is True else (req.debug_break or "")

    def run():
        return agent.stream_agent(GRAPH, req.session_id, req.text.strip(), req.circuit, mode, req.board,
                                  mode=req.mode, via=req.via)

    def events():
        try:
            for event in keys.iterate(user_keys, run):
                if event["type"] == "result":
                    event["llm"] = keys.call(user_keys, lambda: "/".join(provider_info()))
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # report instead of silently cutting the stream
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
