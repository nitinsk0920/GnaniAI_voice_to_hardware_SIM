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
print("ALL OFFLINE TESTS PASSED")
