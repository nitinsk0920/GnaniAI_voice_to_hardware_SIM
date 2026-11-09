# Offline tests (no LLM): preflight rules, break_code, compile line-number shift.
# usage (from D:\gnani): .venv\Scripts\python tests\test_offline.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import graph as g
UNO, NANO = g.BOARDS["uno"], g.BOARDS["nano"]
from compiler import compile_sketch

circ = {"components": [{"type": "led", "id": "LED1", "pin": "13"}, {"type": "led", "id": "LED2", "pin": 9}]}
assert g.check_circuit(circ, UNO) is None and circ["components"][0]["pin"] == 13
bad = [
    ({"components": [{"type": "led", "id": "LED1", "pin": 1}]}, "serial"),
    ({"components": [{"type": "led", "id": "LED1", "pin": 9}, {"type": "led", "id": "LED2", "pin": 9}]}, "two components"),
    ({"components": [{"type": "laser", "id": "L1", "pin": 9}]}, "not allowed"),
    ({"components": [{"type": "led", "id": "led1", "pin": 9}]}, "uppercase"),
    ({"components": [{"type": "led", "id": "LED1", "pin": 20}]}, "not a pin of the Arduino Uno"),
    ({"components": [{"type": "potentiometer", "id": "POT1", "pin": "A6"}]}, "not a pin of the Arduino Uno"),
]
for c, want in bad:
    msg = g.check_circuit(c, UNO); assert msg and want in msg, (c, msg); print("circuit rejected:", msg)

# Nano: A6/A7 exist but are analog-only.
assert g.check_circuit({"components": [{"type": "potentiometer", "id": "POT1", "pin": "a6"}]}, NANO) is None
msg = g.check_circuit({"components": [{"type": "led", "id": "LED1", "pin": "A7"}]}, NANO)
assert msg and "only read analog" in msg, msg; print("nano circuit rejected:", msg)
moved = {"components": [{"type": "led", "id": "LED1", "pin": 13}]}
assert g.move_to_pwm(moved, "", [{"type": "breathe", "target": "LED1"}], NANO) and moved["components"][0]["pin"] == 9
print("board facts (nano):", g.board_facts(NANO).splitlines()[1])

ok_code = "void setup(){ pinMode(LED1_PIN, OUTPUT); }\nvoid loop(){ analogWrite(LED2_PIN, 100); }"
assert g.check_code(ok_code, circ, UNO) is None
cases = [
    ("void setup(){ pinMode(13, OUTPUT);} void loop(){}", "literal pin"),
    ("void setup(){ analogWrite(LED1_PIN, 5);} void loop(){}", "not PWM-capable"),
    ("#include <Wire.h>\nvoid setup(){} void loop(){}", "not allowed"),
    ("void setup(){ Serial.begin(9600);} void loop(){}", "Serial"),
    ("void setup(){ pinMode(BUTTON1_PIN, INPUT);} void loop(){}", "no part called BUTTON1"),
    ("#include <Servo.h>\nServo s; void setup(){ analogWrite(LED2_PIN, 5);} void loop(){}", "Servo library"),
    ("void setup(){ pinMode(LED_BUILTIN, OUTPUT);} void loop(){}", "LED_BUILTIN"),
    ("", "empty"),
]
for code, want in cases:
    msg = g.check_code(code, circ, UNO); assert msg and want in msg, (code, msg); print("code rejected:", msg)

c1 = {"components": [{"type": "led", "id": "LED1", "pin": 13}]}
blink = lambda **kw: [{"type": "blink", "period_ms_min": 150, "period_ms_max": 250, **kw}]
assert "not in the circuit" in g.check_checks(blink(target="LED9"), [], c1)
assert g.check_checks(blink(target="LED1"), [], c1) is None
print("window rejected:", g.check_checks(blink(target="LED1", after_ms=1999, before_ms=0), [], c1))
assert g.check_checks(blink(target="LED1", after_ms=1999, before_ms=0), [], c1)
assert "at least one check" in g.check_checks([], [], c1)

assert g.strip_pin_defines("#define LED1_PIN 4\nvoid setup(){}") == "void setup(){}"
broken = g.break_code("void setup() {\n  pinMode(LED1_PIN, OUTPUT);  // out\n}\nvoid loop() {}\n")
print("broken code:", repr(broken))

block = g.define_block(circ)
code = "void setup() {\n  pinMode(LED1_PIN, OUTPUT)\n}\nvoid loop() {}\n"   # error on line 2 of the LLM code
r = compile_sketch(block + code, line_offset=block.count("\n"))
print("compile error (should mention line 2/3):\n", r["error"])
assert not r["ok"] and ("sketch.ino:2:" in r["error"] or "sketch.ino:3:" in r["error"])

# ---------- chat: line-by-line helpers ----------
sketch = ("// Pins from your circuit (written by the backend)\n#define LED1_PIN 13\n\n"
          "void setup() {\n  pinMode(LED1_PIN, OUTPUT);\n}\n\n// blink\nvoid loop() {\n"
          "  digitalWrite(LED1_PIN, HIGH);\n  delay(500);\n  digitalWrite(LED1_PIN, LOW);\n  delay(500);\n}\n")
assert g.explain_line_numbers(sketch) == [2, 4, 5, 9, 10, 11, 12, 13], g.explain_line_numbers(sketch)
rows = g.line_by_line(sketch, [{"line": 5, "note": "Makes pin 13 an output."}, {"line": "x", "note": "bad"},
                               {"line": 11, "note": 7}, {"line": 99, "note": "not a line"}])
assert [r["line"] for r in rows] == [2, 4, 5, 9, 10, 11, 12, 13]
assert rows[2] == {"line": 5, "code": "pinMode(LED1_PIN, OUTPUT);", "note": "Makes pin 13 an output."}
assert rows[5]["note"] == ""  # a non-text note is dropped, the code line stays
convo = g.conversation_text([{"role": "user", "text": "hi"},
                             {"role": "assistant", "kind": "build", "status": "passed", "text": "Blinks pin 13."}])
assert convo == "User: hi\nAssistant: [built and verified] Blinks pin 13.", convo
assert "Latin letters" in g.script_hint("LED kitni der on rehta hai?")
assert "Devanagari" in g.script_hint("LED को तेज़ blink करो") and "Kannada" in g.script_hint("LED ಬೇಗ blink ಮಾಡು")
print("line-by-line helpers ok")

# ---------- llm.py: retry and backup provider when the provider is busy (fake HTTP, no real calls) ----------
import os, httpx, llm
for setting in ("REQUIRE_USER_KEYS", "RATE_LIMIT"):  # these tests assume local defaults (the Docker image sets both)
    os.environ.pop(setting, None)

class FakeResponse:
    def __init__(self, status, wait=None):
        self.status_code, self.headers = status, ({"retry-after": str(wait)} if wait is not None else {})
        self.text = "fake"
    def json(self):
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

def scripted(*outcomes):
    """Replace httpx.post: each call takes the next outcome (a status code, (429, wait) or "timeout")."""
    log, queue = [], list(outcomes)
    def post(url, **kw):
        log.append("gemini" if "googleapis" in url else "groq")
        o = queue.pop(0)
        if o == "timeout":
            raise httpx.ReadTimeout("slow")
        return FakeResponse(*o) if isinstance(o, tuple) else FakeResponse(o)
    llm.httpx.post = post
    return log

real_post, real_sleep, slept = httpx.post, llm.time.sleep, []
llm.time.sleep = slept.append
saved_env = {k: os.environ.get(k) for k in ("LLM_PROVIDER", "LLM_FALLBACK", "GROQ_API_KEY", "GEMINI_API_KEY")}
os.environ.update(LLM_PROVIDER="groq", LLM_FALLBACK="auto", GROQ_API_KEY="x", GEMINI_API_KEY="y")
msgs = [{"role": "user", "content": "hi"}]
try:
    log = scripted((429, 1), 200); assert llm._post(msgs) and log == ["groq", "groq"] and slept == [1.0]      # short wait: retry
    slept.clear(); log = scripted((429, 15), 200); llm._post(msgs); assert log == ["groq", "gemini"] and not slept  # long wait: backup
    log = scripted("timeout", 200); llm._post(msgs); assert log == ["groq", "gemini"]                         # timeout: backup
    log = scripted(503, 200); llm._post(msgs); assert log == ["groq", "groq"]                                 # 503 without wait: retry
    os.environ["LLM_FALLBACK"] = "none"
    slept.clear(); log = scripted((429, 15), 200); llm._post(msgs); assert log == ["groq", "groq"] and slept == [15.0]  # no backup: wait
    log = scripted((429, 60));
    try: llm._post(msgs); raise AssertionError("should fail")
    except llm.LLMError as e: assert "try again" in str(e) and log == ["groq"]                               # too long, no backup
    os.environ["LLM_FALLBACK"] = "auto"
    log = scripted(401, 200); llm._post(msgs); assert log == ["groq", "gemini"]                               # rejected key: backup
    os.environ["LLM_FALLBACK"] = "none"
    log = scripted(401)
    try: llm._post(msgs); raise AssertionError("should fail")
    except llm.LLMError as e: assert "rejected the API key" in str(e) and "try again" not in str(e) and log == ["groq"]  # no retry
    os.environ["LLM_FALLBACK"] = "auto"
    log = scripted((429, 15), (429, 15))
    try: llm._post(msgs); raise AssertionError("should fail")
    except llm.LLMError as e: assert "backup" in str(e) and log == ["groq", "gemini"]                        # both busy
finally:
    llm.httpx.post, llm.time.sleep = real_post, real_sleep
    for k, v in saved_env.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
print("llm retry / backup provider ok")

# ---------- chat: the whole graph with a scripted fake LLM (real compile + simulation) ----------
import tempfile, db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="gnani_test_")) / "test.db"
calls = []
BLINK = ("void setup() {\n  pinMode(LED1_PIN, OUTPUT);\n}\n\nvoid loop() {\n  digitalWrite(LED1_PIN, HIGH);\n"
         "  delay(500);\n  digitalWrite(LED1_PIN, LOW);\n  delay(500);\n}\n")

def fake_llm(system, user, required_keys=()):
    if system == g.CONVERSE_SYSTEM:
        calls.append("understand")
        latest = user.rsplit("Latest message:", 1)[1]
        assert "Recent conversation" in user
        if "why" in latest:
            assert "  5|   pinMode(LED1_PIN, OUTPUT);" in user and "passed: pin 13 blinks" in user, \
                "chat must see the numbered code and the test results"
            assert "User: blink the LED" in user, "chat must see the conversation"
            return {"intent": "chat", "reply": "Line 8 waits 500 ms.", "lines": "8", "spoken": "It waits half a second."}
        return {"intent": "build", "ack": "Sure, one moment."}
    if system == g.FIX_SYSTEM:
        calls.append("fix")
        assert "FAILED blink" in user and "pin 13" in user, "fix must get the measured failure"
        return {"code": BLINK, "note": "restored 500 ms delays", "say": "It blinked too slowly, fixing it."}
    if system == g.PLAN_SYSTEM:
        calls.append("plan")
        return {"circuit": {"components": [{"type": "led", "id": "LED1", "pin": 13}]}, "code": BLINK, "stimulus": [],
                "checks": [{"type": "blink", "target": "LED1", "period_ms_min": 800, "period_ms_max": 1200}],
                "behavior_summary": "Blinks LED1 once a second."}
    if system == g.EXPLAIN_SYSTEM:
        calls.append("explain")
        return {"summary": "LED1 blinks.", "lines": [{"line": 5, "note": "Pin 13 is an output."}],
                "try_next": ["slower"], "spoken_summary": "The LED on pin thirteen blinks."}
    raise AssertionError("unexpected prompt")

g.chat_json = fake_llm
G = g.build_graph()
c13 = {"components": [{"type": "led", "id": "LED1", "pin": 13}]}
run = lambda text, **kw: [e for e in g.stream_agent(G, "s1", text, json.loads(json.dumps(c13)), **kw) if e["type"] == "result"][0]
import json

r1 = run("blink the LED")
assert r1["kind"] == "build" and r1["status"] == "passed" and r1["hex"], r1.get("problem")
assert calls == ["understand", "plan", "explain"], calls
assert r1["explanation"]["lines"][2] == {"line": 5, "code": "pinMode(LED1_PIN, OUTPUT);", "note": "Pin 13 is an output."}
r2 = run("why does it wait?")
assert r2["kind"] == "chat" and r2["reply"] == "Line 8 waits 500 ms." and r2["lines"] == "8", r2
calls.clear()
r3 = run("slower", mode="build")
assert r3["kind"] == "build" and calls == ["plan", "explain"], calls  # mode build skips understand
snap = g.session_snapshot(G, "s1")
assert [m["role"] for m in snap["messages"]] == ["user", "assistant"] * 3
assert [m.get("kind") for m in snap["messages"][1::2]] == ["build", "chat", "build"]
assert snap["hex"] and snap["full_code"].startswith("// Pins") and snap["board"] == "uno"
assert snap["messages"][1]["card"]["explanation"]["summary"] == "LED1 blinks."
assert g.session_snapshot(G, "unknown")["messages"] == []
print("graph build -> chat -> build ok; session snapshot ok")

# Live narration: the ack and the fix's spoken line arrive as "say" events before the result.
calls.clear()
events = list(g.stream_agent(G, "s2", "blink it", json.loads(json.dumps(c13)), debug_break="timing"))
says = [(e["node"], e["text"]) for e in events if e["type"] == "say"]
result = events[-1]
assert says == [("understand", "Sure, one moment."), ("fix", "It blinked too slowly, fixing it.")], says
assert result["type"] == "result" and result["status"] == "passed" and result["attempts"] == 1, result.get("problem")
assert calls == ["understand", "plan", "fix", "explain"], calls
assert [e["type"] for e in events].index("say") < len(events) - 1
print("live narration (say events) ok")

# ---------- BYOK: the visitor's own keys, per request (keys.py, llm.py, main.py; no real API calls) ----------
import asyncio, keys
saved_env = {k: os.environ.get(k) for k in ("REQUIRE_USER_KEYS", "GROQ_API_KEY", "GEMINI_API_KEY", "GNANI_API_KEY",
                                           "LLM_PROVIDER", "LLM_FALLBACK")}
os.environ.update(GROQ_API_KEY="env-groq", GEMINI_API_KEY="env-gemini", GNANI_API_KEY="env-gnani",
                  LLM_PROVIDER="groq", LLM_FALLBACK="auto")
os.environ.pop("REQUIRE_USER_KEYS", None)
SECRET = "gsk_TEST_SECRET_never_stored_4711"
try:
    assert keys.clean(" gsk_abc ") == "gsk_abc" and keys.clean("a b") == "" and keys.clean("kľúč") == "" \
        and keys.clean("x" * 400) == ""
    assert keys.from_headers({"x-groq-key": "u-groq", "x-gnani-key": " "}) == {"groq": "u-groq"}
    assert keys.get("groq") == "env-groq"                                   # locally: .env
    assert keys.call({"groq": "u-groq"}, keys.get, "groq") == "u-groq"       # the user's key wins
    assert keys.get("gnani", "explicit") == "explicit"
    os.environ["REQUIRE_USER_KEYS"] = "1"                                    # public server: never .env
    assert keys.get("groq") == "" and keys.get("gnani") == "" and not llm.has_key()
    assert keys.call({"gemini": "u-gem"}, llm.provider_info)[0] == "gemini"  # only a Gemini key: use Gemini
    assert keys.call({"gemini": "u-gem"}, llm.fallback_provider, "gemini") is None
    assert keys.call({"groq": "u-g", "gemini": "u-gem"}, llm.fallback_provider, "groq") == "gemini"
    assert ".env" not in keys.missing_message("gnani")
    sent = []
    llm.httpx.post = lambda url, json=None, timeout=None, headers=None: sent.append(headers["Authorization"]) or FakeResponse(200)
    keys.call({"groq": "u-groq"}, llm._post, msgs)
    assert sent == ["Bearer u-groq"], sent
    try: llm._post(msgs); raise AssertionError("should fail without a key")
    except llm.LLMError as e: assert "no LLM API key" in str(e) and not e.bad_output
    llm.httpx.post = real_post
    print("keys: user key > .env, REQUIRE_USER_KEYS ignores .env, provider follows the keys ok")

    # The key reaches every LLM call of a real graph run (LangGraph worker threads) and is never stored.
    seen = []
    def keyed_llm(system, user, required_keys=()):
        seen.append(keys.get("groq"))
        return fake_llm(system, user, required_keys)
    g.chat_json = keyed_llm
    events = list(keys.iterate({"groq": SECRET}, lambda: g.stream_agent(G, "s3", "blink the LED", json.loads(json.dumps(c13)))))
    assert events[-1]["status"] == "passed" and len(seen) == 3 and set(seen) == {SECRET}, (seen, events[-1].get("problem"))
    assert keys.get("groq") == ""                                            # nothing leaks outside the request
    stored = b"".join(f.read_bytes() for f in db.DB_PATH.parent.iterdir())
    assert SECRET.encode() not in stored and b"gsk_TEST" not in stored, "a key must never reach the SQLite file"
    print("key reaches every LLM call in the graph, never stored in SQLite ok")

    # main.py routes (FastAPI test client). Importing main reads .env, but REQUIRE_USER_KEYS keeps it unused.
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import main
    client = TestClient(main.app)
    h = client.get("/health").json()
    assert h["user_keys_required"] and not h["llm_key_set"] and not h["gnani_key_set"], h
    r = client.post("/run", json={"session_id": "s4", "text": "blink", "circuit": c13})
    assert r.status_code == 401 and "API keys" in r.json()["detail"] and ".env" not in r.json()["detail"], r.text
    assert client.post("/speak", json={"text": "hi"}).status_code == 401
    assert client.post("/speak/stream", json={"text": "hi"}).status_code == 401
    assert client.post("/transcribe", content=b"RIFF....", headers={"Content-Type": "audio/wav"}).status_code == 401
    seen.clear()
    with client.stream("POST", "/run", json={"session_id": "s4", "text": "blink the LED", "circuit": c13},
                       headers={"X-Groq-Key": SECRET}) as r:
        body = r.read().decode()
    assert r.status_code == 200 and '"status": "passed"' in body and set(seen) == {SECRET}, body[-300:]
    assert SECRET not in body

    # Live mode relay: the key comes in the first WebSocket message; other sites may not use the relay.
    upstream_headers = []
    class FakeUpstream:
        close_code = 1000
        async def send(self, data): pass
        async def close(self): pass
        def __aiter__(self): return self.messages()
        async def messages(self):
            yield '{"type": "connected"}'
    async def fake_connect(url, additional_headers=None, open_timeout=None):
        upstream_headers.append(additional_headers)
        return FakeUpstream()
    real_connect, main.websockets.connect = main.websockets.connect, fake_connect
    try:
        try:
            with client.websocket_connect("/live/stt", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_text(); raise AssertionError("another site must be refused")
        except WebSocketDisconnect as e:
            assert e.code == 1008
        with client.websocket_connect("/live/stt", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_bytes(bytes(1024))                                       # no auth message, no .env key
            assert "Gnani API key" in ws.receive_json()["message"]
        with client.websocket_connect("/live/stt", headers={"origin": "http://testserver"}) as ws:  # same host
            ws.send_text(json.dumps({"type": "auth", "gnani_key": "user-gnani"}))
            assert ws.receive_json()["type"] == "connected"
        assert len(upstream_headers) == 1 and upstream_headers[0]["x-api-key-id"] == "user-gnani"
    finally:
        main.websockets.connect = real_connect
    print("routes: 401 without keys, keys from headers, Live relay key + origin check ok")

    # Public server limits (limits.py): per visitor, and builds at the same time (slots expire).
    import limits
    from fastapi import HTTPException
    limits.check("run", "1.2.3.4")                                           # off by default: never counts
    assert not limits._hits and limits.start_build() is None
    os.environ["RATE_LIMIT"] = "1"
    for i in range(20):
        limits.check("run", "1.2.3.4", now=1000 + i)
    try: limits.check("run", "1.2.3.4", now=1100); raise AssertionError("21st request must be refused")
    except HTTPException as e: assert e.status_code == 429 and "try again in" in e.detail
    limits.check("run", "5.6.7.8", now=1100)                                 # another visitor is fine
    limits.check("run", "1.2.3.4", now=1000 + 600 + 1)                       # the window moved on
    assert limits.client_ip({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "10.0.0.2") == "9.9.9.9"
    slots = [limits.start_build(now=50) for _ in range(limits.MAX_PARALLEL_BUILDS)]
    try: limits.start_build(now=60); raise AssertionError("must be busy")
    except HTTPException as e: assert e.status_code == 503
    limits.end_build(slots[0]); limits.end_build(limits.start_build(now=61))   # a released slot is reusable
    assert limits.start_build(now=50 + limits.BUILD_MAX_S + 1)               # stuck slots expire
    assert main.origin_allowed("https://me-app.hf.space", "10.0.0.5:7860", "me-app.hf.space")
    assert not main.origin_allowed("https://evil.example", "me-app.hf.space", "me-app.hf.space")
    limits._hits.clear(); limits._builds.clear(); os.environ.pop("RATE_LIMIT")
    print("public server limits ok")
finally:
    g.chat_json = fake_llm
    llm.httpx.post = real_post
    for k, v in saved_env.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
print("ALL OFFLINE TESTS PASSED")
