# Arduino Mega 2560 under avr8js: measure every PWM pin and timer, servo (Timer5 interrupts), tone (Timer2),
# delay/millis (Timer0 overflow), buttons on port A, analog A0 and A8 (MUX5), and external interrupts.
# usage (from D:\gnani): .venv\Scripts\python tests\test_mega.py      (no LLM, no servers; about 30 s)
import statistics
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import graph as g
from compiler import compile_sketch
from evaluator import duty, rising_edges, high_pulses, servo_angle, evaluate

MEGA = g.BOARDS["mega"]
ok_all = True


def run(code, watch, ms=1500, inputs=(), analog=(), events=()):
    r = compile_sketch(code, fqbn=MEGA["fqbn"])
    assert r["ok"], r["error"]
    config = {"mcu": MEGA["mcu"], "duration_ms": ms, "watch": [g.pin_ref(p, MEGA) for p in watch],
              "inputs": list(inputs), "analog": list(analog), "events": list(events)}
    m, err = g.run_simulation(r["hex"], config)
    assert not err, err
    return m


def report(name, ok, detail):
    global ok_all
    ok_all &= ok
    print(f"{'OK ' if ok else 'BAD'} {name}: {detail}")


# 1. Every PWM pin: analogWrite a different value on each and measure duty and frequency.
pwm = [int(p) for p in g.pwm_pins(MEGA)]
values = {p: 20 + 15 * i for i, p in enumerate(pwm)}
code = "void setup(){\n" + "".join(f"  pinMode({p},OUTPUT); analogWrite({p},{v});\n" for p, v in values.items()) + "}\nvoid loop(){}\n"
m = run(code, pwm, ms=600)
for p in pwm:
    e = m["pins"][str(p)]
    d = duty(e, 200, 600)
    rises = rising_edges(e, 200, 600)
    freq = 1000 / statistics.median([b - a for a, b in zip(rises, rises[1:])]) if len(rises) > 2 else 0
    want = values[p] / 255
    timer = MEGA["pins"][str(p)]["timer"]
    want_hz = 976 if timer == 0 else 490
    report(f"PWM pin {p} (Timer{timer})", abs(d - want) < 0.02 and abs(freq - want_hz) < 0.1 * want_hz,
           f"duty {d:.3f} (want {want:.3f}), {freq:.0f} Hz (want ~{want_hz})")

# 2. delay()/millis() (Timer0 overflow interrupt) on a pin of port A.
m = run("void setup(){pinMode(22,OUTPUT);} void loop(){digitalWrite(22,HIGH);delay(250);digitalWrite(22,LOW);delay(250);}", [22], ms=2200)
r = rising_edges(m["pins"]["22"], 100, 2200)
period = statistics.median([b - a for a, b in zip(r, r[1:])])
report("blink on pin 22 (delay 250/250)", abs(period - 500) < 5, f"period {period:.1f} ms (want 500)")

# 3. Servo on pin 9 (Servo library uses Timer5 compare interrupts on the Mega).
m = run("#include <Servo.h>\nServo s; void setup(){s.attach(9);} void loop(){unsigned long t=millis(); s.write(t<800?0:(t<1600?90:180));}", [9], ms=2400)
e = m["pins"]["9"]
for a, b, want in [(300, 800, 0), (1100, 1600, 90), (1900, 2400, 180)]:
    ang = servo_angle(statistics.median(high_pulses(e, a, b)))
    report(f"servo pin 9 at {want} deg", abs(ang - want) < 3, f"{ang:.1f} deg")

# 4. tone() on pin 8 (Timer2 compare interrupt toggles the pin).
m = run("void setup(){tone(8,440);} void loop(){}", [8], ms=800)
r = rising_edges(m["pins"]["8"], 100, 800)
f = 1000 / statistics.median([b - a for a, b in zip(r, r[1:])])
report("tone 440 Hz on pin 8", abs(f - 440) < 5, f"{f:.1f} Hz")

# 5. Button on pin 23 (port A) controls LED on pin 13.
btn = g.pin_ref(23, MEGA)
m = run("void setup(){pinMode(23,INPUT_PULLUP);pinMode(13,OUTPUT);} void loop(){digitalWrite(13, digitalRead(23)==LOW);}", [13], ms=1500,
        inputs=[{**btn, "level": True}],
        events=[{"at_ms": 500, "kind": "pin", "port": btn["port"], "bit": btn["bit"], "level": False},
                {"at_ms": 1000, "kind": "pin", "port": btn["port"], "bit": btn["bit"], "level": True}])
report("button on pin 23 -> LED 13", [lv for _, lv in m["pins"]["13"]] == [0, 1, 0], str(m["pins"]["13"]))

# 6. Potentiometers on A0 and A8 (A8 needs the MUX5 bit) set LED brightness on pins 11 and 12.
m = run("void setup(){} void loop(){analogWrite(11, analogRead(A0)/4); analogWrite(12, analogRead(A8)/4);}", [11, 12], ms=800,
        analog=[{"channel": 0, "volts": 1.25}, {"channel": 8, "volts": 3.75}])
d0, d8 = duty(m["pins"]["11"], 300, 800), duty(m["pins"]["12"], 300, 800)
report("pot A0 = 1.25 V -> pin 11", abs(d0 - 0.25) < 0.02, f"duty {d0:.3f} (want 0.25)")
report("pot A8 = 3.75 V -> pin 12", abs(d8 - 0.75) < 0.02, f"duty {d8:.3f} (want 0.75)")

# 7. External interrupts: pin 2 is INT4 (port E), pin 21 is INT0 (port D). Each press toggles an LED.
code = """volatile bool a=false, b=false;
void fa(){a=!a;} void fb(){b=!b;}
void setup(){pinMode(2,INPUT_PULLUP);pinMode(21,INPUT_PULLUP);pinMode(30,OUTPUT);pinMode(31,OUTPUT);
  attachInterrupt(digitalPinToInterrupt(2),fa,FALLING); attachInterrupt(digitalPinToInterrupt(21),fb,FALLING);}
void loop(){digitalWrite(30,a);digitalWrite(31,b);}"""
p2, p21 = g.pin_ref(2, MEGA), g.pin_ref(21, MEGA)
press = lambda ref, t: [{"at_ms": t, "kind": "pin", "port": ref["port"], "bit": ref["bit"], "level": False},
                        {"at_ms": t + 100, "kind": "pin", "port": ref["port"], "bit": ref["bit"], "level": True}]
m = run(code, [30, 31], ms=1500, inputs=[{**p2, "level": True}, {**p21, "level": True}],
        events=press(p2, 300) + press(p2, 700) + press(p21, 500))
levels30 = [lv for _, lv in m["pins"]["30"]]
levels31 = [lv for _, lv in m["pins"]["31"]]
report("attachInterrupt pin 2 (INT4), 2 presses", levels30 == [0, 1, 0], str(m["pins"]["30"]))
report("attachInterrupt pin 21 (INT0), 1 press", levels31 == [0, 1], str(m["pins"]["31"]))

print("\nALL MEGA CHECKS PASSED" if ok_all else "\nSOME MEGA CHECKS FAILED")
