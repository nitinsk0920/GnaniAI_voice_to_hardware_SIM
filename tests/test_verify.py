# Offline verify tests: preflight -> compile -> sim_config -> verify.mjs -> evaluate. No LLM.
# usage (from D:\gnani): .venv\Scripts\python tests\test_verify.py
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import graph as g
UNO, NANO = g.BOARDS["uno"], g.BOARDS["nano"]
from compiler import compile_sketch
from evaluator import evaluate

def verify(name, comps, checks, stimulus, code, expect_pass, board=UNO):
    circuit = {"components": comps}
    assert g.check_circuit(circuit, board) is None
    p = g.check_checks(checks, stimulus, circuit) or g.check_code(code, circuit, board)
    assert p is None, p
    r = compile_sketch(g.define_block(circuit) + code, fqbn=board["fqbn"])
    assert r["ok"], r["error"]
    cfg, resolved = g.sim_config(circuit, checks, stimulus, board)
    t = time.perf_counter()
    m, err = g.run_simulation(r["hex"], cfg)
    assert not err, err
    rep = evaluate(resolved, m)
    flag = "OK  " if rep["passed"] == expect_pass else "BAD "
    print(f"{flag}{name}: passed={rep['passed']} (expected {expect_pass}) sim={cfg['duration_ms']}ms wall={time.perf_counter()-t:.1f}s")
    for x in rep["results"]:
        print(f"      {'✓' if x['ok'] else '✗'} {x['check']}: {x['detail']}")
    return rep["passed"] == expect_pass

LED13 = [{"type": "led", "id": "LED1", "pin": 13}]
LED9 = [{"type": "led", "id": "LED1", "pin": 9}]
results = []
blink2s = [{"type": "blink", "target": "LED1", "period_ms_min": 1500, "period_ms_max": 2500}]
results.append(verify("slow blink, correct", LED13, blink2s, [],
    "void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){digitalWrite(LED1_PIN,HIGH);delay(1000);digitalWrite(LED1_PIN,LOW);delay(1000);}", True))
results.append(verify("slow blink, toggles every 2 s (the LLM's usual mistake)", LED13, blink2s, [],
    "unsigned long p=0; bool s=false; void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){ if(millis()-p>=2000){p=millis(); s=!s; digitalWrite(LED1_PIN,s);} }", False))

breathe = [{"type": "breathe", "target": "LED1", "min_duty_below": 0.2, "max_duty_above": 0.8, "period_ms_min": 1500, "period_ms_max": 3000}]
results.append(verify("breathe pin 9, correct", LED9, breathe, [],
    "void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){for(int i=0;i<256;i++){analogWrite(LED1_PIN,i);delay(4);} for(int i=255;i>=0;i--){analogWrite(LED1_PIN,i);delay(4);}}", True))
results.append(verify("breathe, but only fades up to half brightness", LED9, breathe, [],
    "void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){for(int i=0;i<128;i++){analogWrite(LED1_PIN,i);delay(8);} for(int i=127;i>=0;i--){analogWrite(LED1_PIN,i);delay(8);}}", False))

btn = LED13 + [{"type": "button", "id": "BUTTON1", "pin": 2}]
btn_checks = [{"type": "steady", "target": "LED1", "level": "low", "before_ms": 2000},
              {"type": "blink", "target": "LED1", "period_ms_min": 150, "period_ms_max": 250, "after_ms": 2300}]
press = [{"target": "BUTTON1", "at_ms": 2000, "action": "press"}]
results.append(verify("button press starts fast blink", btn, btn_checks, press,
    "bool on=false; void setup(){pinMode(LED1_PIN,OUTPUT);pinMode(BUTTON1_PIN,INPUT_PULLUP);} void loop(){ if(digitalRead(BUTTON1_PIN)==LOW) on=true; if(on){digitalWrite(LED1_PIN,HIGH);delay(100);digitalWrite(LED1_PIN,LOW);delay(100);} }", True))
results.append(verify("button, but code reads it inverted (blinks before press)", btn, btn_checks, press,
    "void setup(){pinMode(LED1_PIN,OUTPUT);pinMode(BUTTON1_PIN,INPUT_PULLUP);} void loop(){ if(digitalRead(BUTTON1_PIN)==HIGH){digitalWrite(LED1_PIN,HIGH);delay(100);digitalWrite(LED1_PIN,LOW);delay(100);} }", False))

servo = [{"type": "servo", "id": "SERVO1", "pin": 6}]
results.append(verify("servo sweep", servo, [{"type": "servo_sweep", "target": "SERVO1", "min_angle_below": 10, "max_angle_above": 170}], [],
    "#include <Servo.h>\nServo s; void setup(){s.attach(SERVO1_PIN);} void loop(){for(int a=0;a<=180;a+=2){s.write(a);delay(10);} for(int a=180;a>=0;a-=2){s.write(a);delay(10);}}", True))
results.append(verify("servo to 90", servo, [{"type": "servo_angle", "target": "SERVO1", "angle_deg": 90, "tolerance_deg": 5}], [],
    "#include <Servo.h>\nServo s; void setup(){s.attach(SERVO1_PIN); s.write(90);} void loop(){}", True))

buz = [{"type": "buzzer", "id": "BUZZER1", "pin": 8}]
results.append(verify("buzzer 440 Hz", buz, [{"type": "tone", "target": "BUZZER1", "freq_min": 400, "freq_max": 480}], [],
    "void setup(){tone(BUZZER1_PIN,440);} void loop(){}", True))

pot = LED9 + [{"type": "potentiometer", "id": "POT1", "pin": "A0"}]
results.append(verify("pot controls brightness", pot,
    [{"type": "duty", "target": "LED1", "min": 0.0, "max": 0.1, "after_ms": 700, "before_ms": 1900},
     {"type": "duty", "target": "LED1", "min": 0.85, "max": 1.0, "after_ms": 2200}],
    [{"target": "POT1", "at_ms": 500, "action": "set", "value": 0}, {"target": "POT1", "at_ms": 2000, "action": "set", "value": 1023}],
    "void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){analogWrite(LED1_PIN, analogRead(POT1_PIN)/4);}", True))

# Arduino Nano (compiled with arduino:avr:nano), including the Nano-only analog pin A6.
results.append(verify("NANO: slow blink", LED13, blink2s, [],
    "void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){digitalWrite(LED1_PIN,HIGH);delay(1000);digitalWrite(LED1_PIN,LOW);delay(1000);}", True, NANO))
nano_pot = LED9 + [{"type": "potentiometer", "id": "POT1", "pin": "A6"}]
results.append(verify("NANO: pot on A6 controls brightness", nano_pot,
    [{"type": "duty", "target": "LED1", "min": 0.0, "max": 0.1, "from_ms": 700, "to_ms": 1900},
     {"type": "duty", "target": "LED1", "min": 0.85, "max": 1.0, "from_ms": 2200}],
    [{"target": "POT1", "at_ms": 500, "action": "set", "value": 0}, {"target": "POT1", "at_ms": 2000, "action": "set", "value": 1023}],
    "void setup(){pinMode(LED1_PIN,OUTPUT);} void loop(){analogWrite(LED1_PIN, analogRead(POT1_PIN)/4);}", True, NANO))

print("\nALL AS EXPECTED" if all(results) else "\nSOME RESULTS WERE NOT AS EXPECTED")
