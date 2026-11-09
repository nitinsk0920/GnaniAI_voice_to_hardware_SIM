"""Gnani voice APIs: Prisma (speech-to-text) and Timbre (text-to-speech).

Every Gnani call goes through this file. Each function takes the caller's key (BYOK: the visitor's
own key, sent with the request); without one, keys.get falls back to .env unless REQUIRE_USER_KEYS is set.
Docs: https://docs.gnani.ai
  STT REST:      POST /stt/v3                (one clip, max 30 s; the mic button)
  STT streaming: WSS  /stt/v3/stream         (Live mode; the server detects start/end of speech)
  TTS REST:      POST /api/v1/tts/inference  (one MP3)
  TTS streaming: POST /api/v1/tts/sse        (Live mode; base64 audio chunks as they are generated)
.env: GNANI_API_KEY (used when the request brings no key), GNANI_TTS_VOICE (optional, default Shivani)
"""
import base64
import json
import os
import re

import httpx

import keys

BASE_URL = "https://api.vachana.ai"
STT_MODEL = "prisma-v3.0"
STT_LANGUAGES = "en-IN,hi-IN,kn-IN"   # our demo languages; Prisma v3.0 accepts up to 4 as hints
STT_KEYTERMS = "LED,servo,buzzer,potentiometer,button,blink,breathe,pin,Arduino"
TTS_MODEL = "timbre-v3.0"             # every v3.0 voice speaks all 10 languages
TIMEOUT_S = 30

STT_STREAM_URL = "wss://api.vachana.ai/stt/v3/stream"
TTS_STREAM_RATE = 24000               # raw 16-bit mono PCM; the browser plays it as it arrives


class GnaniError(Exception):
    pass


def _key(key=None):
    key = keys.get("gnani", key)
    if not key:
        raise GnaniError(keys.missing_message("gnani"))
    return key


def _error_text(r):
    """Gnani has three error shapes (STT, TTS flat, gateway). Return a short readable message."""
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    if isinstance(body.get("detail"), dict):        # 401/402/403/429 gateway errors
        d = body["detail"]
        return f"HTTP {r.status_code} {d.get('error_code', '')}: {d.get('message', '')}".strip()
    if isinstance(body.get("error"), dict):         # STT errors
        return f"HTTP {r.status_code} {body['error'].get('type', '')}: {body['error'].get('message', '')}"
    if "message" in body:                           # TTS flat errors
        return f"HTTP {r.status_code}: {body['message']}"
    return f"HTTP {r.status_code}: {str(body)[:200]}"


def transcribe(audio_bytes, filename="speech.wav", key=None):
    """Prisma REST (max 30 s). Returns {"transcript", "language"}.
    format=codemix: Kannada/Hindi in their own script, English words (LED, servo) stay in Latin letters."""
    try:
        r = httpx.post(
            f"{BASE_URL}/stt/v3",
            headers={"X-API-Key-ID": _key(key)},
            data={"model": STT_MODEL, "language_code": STT_LANGUAGES, "format": "codemix",
                  "keyterms": STT_KEYTERMS},
            files={"audio_file": (filename, audio_bytes, "audio/wav")},
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as e:
        raise GnaniError(f"could not reach Gnani speech-to-text: {e}")
    if r.status_code != 200:
        raise GnaniError(f"speech-to-text failed: {_error_text(r)}")
    body = r.json()
    if not body.get("success", True):
        raise GnaniError(f"speech-to-text failed: {body}")
    return {"transcript": (body.get("transcript") or "").strip(),
            "language": body.get("detected_language_code")}


def language_of(text):
    """Pick the Timbre language from the script: Kannada letters -> kn-IN, Devanagari -> hi-IN, else en-IN."""
    kannada = len(re.findall(r"[ಀ-೿]", text))
    devanagari = len(re.findall(r"[ऀ-ॿ]", text))
    if kannada > devanagari and kannada > 0:
        return "kn-IN"
    if devanagari > 0:
        return "hi-IN"
    return "en-IN"


def stt_stream_headers(key=None):
    """WebSocket upgrade headers for Prisma streaming. Tested Nov 9:
    - the stream refuses a list of languages ("exactly one locale is required"), so indic-auto lets
      Prisma v3.0 pick English, Hindi or Kannada per utterance (it returns detected_language_code);
    - VAD from the docs' "clean web microphone" preset: speech threshold 0.5, 700 ms of silence ends a turn;
    - idle timeout raised to 15 min (the session cap), so thinking pauses do not close it."""
    return {
        "x-api-key-id": _key(key),
        "model": STT_MODEL,
        "x-model": STT_MODEL,           # older spelling, also accepted
        "language_code": "indic-auto",
        "format": "codemix",
        "keyterms": STT_KEYTERMS,
        "x-sample-rate": "16000",
        "x-vad-threshold": "0.5",
        "x-min-silence-ms": "700",
        "x-idle-timeout-sec": "900",
    }


def speak_stream(text, language=None, rate=TTS_STREAM_RATE, key=None):
    """Timbre SSE. Yields raw 16-bit mono PCM chunks (`rate` Hz) while the speech is generated,
    so playback can start after about 1.5 s instead of waiting for the whole clip."""
    text = (text or "").strip()
    if not text:
        raise GnaniError("nothing to speak")
    body = {
        "text": text[:600],
        "model": TTS_MODEL,
        "voice": os.getenv("GNANI_TTS_VOICE", "Shivani"),
        "language": language or language_of(text),
        "speed": 1.0,
        "audio_config": {"sample_rate": rate, "num_channels": 1, "sample_width": 2,
                         "encoding": "linear_pcm", "container": "raw"},
    }
    try:
        with httpx.stream("POST", f"{BASE_URL}/api/v1/tts/sse", json=body, timeout=TIMEOUT_S,
                          headers={"X-API-Key-ID": _key(key), "Content-Type": "application/json"}) as r:
            if r.status_code != 200:
                r.read()
                raise GnaniError(f"text-to-speech failed: {_error_text(r)}")
            event = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:].strip() or "{}")
                    if event == "error":
                        raise GnaniError(f"text-to-speech failed: {data.get('message', data)}")
                    if data.get("audio"):
                        yield base64.b64decode(data["audio"])
    except httpx.HTTPError as e:
        raise GnaniError(f"could not reach Gnani text-to-speech: {e}")


def speak(text, language=None, key=None):
    """Timbre REST. Returns (audio bytes, content type). MP3 keeps the download small."""
    text = (text or "").strip()
    if not text:
        raise GnaniError("nothing to speak")
    try:
        r = httpx.post(
            f"{BASE_URL}/api/v1/tts/inference",
            headers={"X-API-Key-ID": _key(key), "Content-Type": "application/json"},
            json={
                "text": text[:600],
                "model": TTS_MODEL,
                "voice": os.getenv("GNANI_TTS_VOICE", "Shivani"),
                "language": language or language_of(text),
                "speed": 1.0,
                "audio_config": {"sample_rate": 24000, "num_channels": 1, "sample_width": 2,
                                 "container": "mp3", "bitrate": "96k"},
            },
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as e:
        raise GnaniError(f"could not reach Gnani text-to-speech: {e}")
    if r.status_code != 200:
        raise GnaniError(f"text-to-speech failed: {_error_text(r)}")
    return r.content, r.headers.get("content-type", "audio/mpeg")
