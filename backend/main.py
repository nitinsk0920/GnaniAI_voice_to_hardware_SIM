"""Voice-to-Hardware Simulator backend.

Run from the backend/ folder, inside the venv:
    uvicorn main:app --reload --port 8000
"""
import json
import os
import shutil
import time
from pathlib import Path
from typing import Literal, Optional, Union

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")  # before importing modules that read env

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import gnani
import graph as agent
from compiler import compile_sketch
from llm import LLMError, provider_info

MAX_CODE_BYTES = 8_000
MAX_AUDIO_BYTES = 2_000_000  # 30 s of 16 kHz mono 16-bit WAV is about 1 MB

app = FastAPI(title="Voice-to-Hardware Simulator")

# Allow the Vite dev server (any localhost port) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

GRAPH = agent.build_graph()


class CompileRequest(BaseModel):
    code: str
    board: str = agent.DEFAULT_BOARD


def board_or_400(board_id):
    if board_id not in agent.BOARDS:
        raise HTTPException(status_code=400, detail=f"unknown board '{board_id}' (have: {sorted(agent.BOARDS)})")
    return agent.BOARDS[board_id]


class RunRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    circuit: dict
    # Dev switch: "compile" breaks the code before the first compile; "timing" makes it 4x too slow
    # so the measured check fails. true is accepted as "compile" (Phase 2 clients).
    debug_break: Union[bool, Literal["", "compile", "timing"]] = ""
    board: str = agent.DEFAULT_BOARD


@app.get("/health")
def health():
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
        "gnani_key_set": bool(os.getenv("GNANI_API_KEY")),
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
    try:
        return gnani.transcribe(audio)
    except gnani.GnaniError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/speak")
def speak_route(req: SpeakRequest):
    """Text in, audio out (Timbre). Only the short spoken summary is sent here, never code."""
    try:
        audio, content_type = gnani.speak(req.text, req.language)
    except gnani.GnaniError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=audio, media_type=content_type)


@app.get("/boards")
def boards():
    return [{"id": b["id"], "name": b["name"], "chip": b["chip"]} for b in agent.BOARDS.values()]


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
def run_route(req: RunRequest):
    """Streams Server-Sent Events: {"type":"step",...} while working, then {"type":"result",...}."""
    # The user's circuit must be valid before the agent starts.
    problem = agent.check_circuit(req.circuit, board_or_400(req.board))
    if problem:
        raise HTTPException(status_code=422, detail=f"circuit: {problem}")

    mode = "compile" if req.debug_break is True else (req.debug_break or "")

    def events():
        try:
            for event in agent.stream_agent(GRAPH, req.session_id, req.text.strip(), req.circuit, mode, req.board):
                if event["type"] == "result":
                    event["llm"] = "/".join(provider_info())
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # report instead of silently cutting the stream
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
