"""Gnani voice APIs: Prisma (speech-to-text) and Timbre (text-to-speech).

Every Gnani call goes through this file (later, BYOK: pass a per-request key instead of .env).
Docs: https://docs.gnani.ai  (STT REST: POST /stt/v3, TTS REST: POST /api/v1/tts/inference)
.env: GNANI_API_KEY (required), GNANI_TTS_VOICE (optional, default Shivani)
"""
import os
import re

import httpx

BASE_URL = "https://api.vachana.ai"
STT_MODEL = "prisma-v3.0"
STT_LANGUAGES = "en-IN,hi-IN,kn-IN"   # our demo languages; Prisma v3.0 accepts up to 4 as hints
STT_KEYTERMS = "LED,servo,buzzer,potentiometer,button,blink,breathe,pin,Arduino"
TTS_MODEL = "timbre-v3.0"             # every v3.0 voice speaks all 10 languages
TIMEOUT_S = 30


class GnaniError(Exception):
    pass


def _key():
    key = os.getenv("GNANI_API_KEY")
    if not key:
        raise GnaniError("GNANI_API_KEY is missing from .env")
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


def transcribe(audio_bytes, filename="speech.wav"):
    """Prisma REST (max 30 s). Returns {"transcript", "language"}.
    format=codemix: Kannada/Hindi in their own script, English words (LED, servo) stay in Latin letters."""
    try:
        r = httpx.post(
            f"{BASE_URL}/stt/v3",
            headers={"X-API-Key-ID": _key()},
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


def speak(text, language=None):
    """Timbre REST. Returns (audio bytes, content type). MP3 keeps the download small."""
    text = (text or "").strip()
    if not text:
        raise GnaniError("nothing to speak")
    try:
        r = httpx.post(
            f"{BASE_URL}/api/v1/tts/inference",
            headers={"X-API-Key-ID": _key(), "Content-Type": "application/json"},
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
