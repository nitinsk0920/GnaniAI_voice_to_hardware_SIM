# Check a running server with "bring your own key": the keys from .env are sent the way the browser
# sends a visitor's keys (headers, and the first Live WebSocket message). Prints results, never keys.
# Costs a little: one agent build (LLM), one short Timbre line, one brief Prisma stream connection.
# usage (from the project folder):
#   .venv\Scripts\python tests\check_byok.py http://localhost:8010
#   .venv\Scripts\python tests\check_byok.py https://<your-space>.hf.space
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
headers = {h: env[k] for h, k in (("X-Groq-Key", "GROQ_API_KEY"), ("X-Gemini-Key", "GEMINI_API_KEY"),
                                   ("X-Gnani-Key", "GNANI_API_KEY")) if env.get(k)}
print("sending keys:", ", ".join(headers) or "none")

health = httpx.get(f"{base}/health", timeout=30).json()
print("health:", {k: health.get(k) for k in ("user_keys_required", "llm_key_set", "gnani_key_set", "arduino_cli", "node")})

# 1. The agent with the visitor's LLM keys (a real build: plan, compile, simulate, explain).
circuit = {"components": [{"type": "led", "id": "LED1", "pin": 13}]}
start = time.time()
result = None
with httpx.stream("POST", f"{base}/run", headers=headers, timeout=240,
                  json={"session_id": f"byok-check-{int(start)}", "text": "blink the LED once a second",
                        "circuit": circuit, "mode": "build"}) as r:
    if r.status_code != 200:
        r.read()
        print("run: HTTP", r.status_code, r.text[:300])
    for line in r.iter_lines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event["type"] in ("result", "error"):
                result = event
if result:
    print(f"run: {result.get('status') or result.get('message')} via {result.get('llm')} in {time.time() - start:.1f} s")

# 2. Timbre streaming with the visitor's Gnani key (one short line).
start = time.time()
with httpx.stream("POST", f"{base}/speak/stream", headers=headers, json={"text": "Keys work."}, timeout=60) as r:
    audio = r.read()
print(f"speak/stream: HTTP {r.status_code}, {len(audio)} bytes of audio in {time.time() - start:.1f} s"
      + ("" if r.status_code == 200 else f": {audio[:200]!r}"))


# 3. Live mode: the Gnani key in the first WebSocket message, then wait for Prisma's "connected".
async def live():
    url = base.replace("http", "ws", 1) + "/live/stt"
    async with websockets.connect(url, origin=base, open_timeout=20) as ws:
        await ws.send(json.dumps({"type": "auth", "gnani_key": env.get("GNANI_API_KEY", "")}))
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        print("live/stt:", message.get("type"), message.get("message", ""))

asyncio.run(live())
