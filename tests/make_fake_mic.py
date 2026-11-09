# Build the fake-microphone WAV files for the Live-mode browser tests (tests/ui_live*.json).
# The spoken phrases come from Timbre once (2 short calls) and are cached in out/live-probe/*.pcm,
# so running this again costs nothing.   usage: .venv\Scripts\python tests\make_fake_mic.py
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import gnani

OUT = ROOT / "out" / "live-probe"
RATE = 16000
SECOND = RATE * 2  # bytes of 16-bit mono audio per second


def phrase(name, text):
    """16 kHz PCM of `text`, from the cache or from Timbre."""
    cached = OUT / f"{name}.pcm"
    if not cached.exists():
        print(f"asking Timbre for: {text}")
        cached.write_bytes(b"".join(gnani.speak_stream(text, "en-IN", rate=RATE)))
    return cached.read_bytes()


def write_wav(name, audio):
    audio = audio[: len(audio) - len(audio) % 2]
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio)
    print(f"wrote {OUT / name} ({len(audio) / SECOND:.1f} s)")


OUT.mkdir(parents=True, exist_ok=True)
build = phrase("probe_tts_16k", "Make the LED on pin nine breathe slowly.")
question = phrase("probe_question_16k", "Wait, what does line five do?")

# ui_live.json: 2 s of silence, the request, then silence.
write_wav("fake_mic.wav", bytes(2 * SECOND) + build + bytes(20 * SECOND))

# ui_live_interrupt.json: the request, then a question at 9.5 s, while the agent is speaking.
audio = bytes(2 * SECOND) + build
audio += bytes(max(0, int(9.5 * SECOND) - len(audio)))
write_wav("fake_mic_interrupt.wav", audio + question + bytes(60 * SECOND))
