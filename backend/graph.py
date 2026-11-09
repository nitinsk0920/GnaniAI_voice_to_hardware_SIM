"""The agent loop as a LangGraph graph.

    understand -> chat: answer the question directly -> respond
               -> build: plan_and_code -> preflight -> compile -> verify -> explain -> respond
    any failure   -> fix -> preflight -> compile -> verify ...   (max 3 fixes, about 90 s)
    out of tries, or a provider/simulator error -> give_up -> respond

The LLM never decides success: verify runs the hex headlessly (verify/verify.mjs) and
evaluator.py compares the measured pins with the checks.
State is checkpointed in SQLite per session: a follow-up ("slower") sees the last code, and the
conversation (`messages`) lets the user ask questions about it in a chat.
"""
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

import db
from compiler import compile_sketch, normalize_error
from evaluator import evaluate
from llm import LLMError, chat_json

MAX_FIXES = 3
TIME_CAP_S = 90
MAX_CODE_LINES = 120
MAX_CODE_BYTES = 8_000
MIN_SIM_MS, MAX_SIM_MS = 4000, 12000
VERIFY_SCRIPT = Path(__file__).parent / "verify" / "verify.mjs"

# Board profiles (backend/boards/*.json): compiler setting, picture, power pins and pin map.
BOARDS = {p.stem: json.loads(p.read_text()) for p in sorted((Path(__file__).parent / "boards").glob("*.json"))}
DEFAULT_BOARD = "uno"


def get_board(board_id):
    return BOARDS.get(board_id or DEFAULT_BOARD) or BOARDS[DEFAULT_BOARD]


def pwm_pins(board):
    return [int(p) for p, info in board["pins"].items() if info["pwm"]]


def digital_pins(board):
    """Pins usable as digital I/O: not serial, not analog-only (A6/A7 on the Nano)."""
    return [p for p, info in board["pins"].items() if info.get("port") and not info.get("reserved")]


def analog_pins(board):
    return [p for p, info in board["pins"].items() if info.get("analog")]


def board_facts(board):
    """The board-specific facts the LLM needs, sent with every plan and fix request."""
    return (f"Board: {board['name']} ({board['chip']}).\n"
            f"Digital pins: {', '.join(digital_pins(board))}. PWM (~) pins for analogWrite: {pwm_pins(board)}. "
            f"Analog inputs: {', '.join(analog_pins(board))}.\n"
            f"Notes: {' '.join(board['notes'])}")
COMPONENT_TYPES = {"led", "button", "servo", "buzzer", "potentiometer"}
OUTPUT_TYPES = {"led", "servo", "buzzer"}

# Check type -> (required fields, part types it can target)
CHECKS = {
    "steady":      ({"target", "level"}, {"led", "buzzer"}),
    "blink":       ({"target", "period_ms_min", "period_ms_max"}, {"led", "buzzer"}),
    "breathe":     ({"target", "min_duty_below", "max_duty_above", "period_ms_min", "period_ms_max"}, {"led"}),
    "duty":        ({"target", "min", "max"}, {"led"}),
    "servo_angle": ({"target", "angle_deg"}, {"servo"}),
    "servo_sweep": ({"target", "min_angle_below", "max_angle_above"}, {"servo"}),
    "tone":        ({"target", "freq_min", "freq_max"}, {"buzzer"}),
}


class State(TypedDict, total=False):
    session_id: str
    board: str                # board profile id (backend/boards/<id>.json)
    user_text: str
    user_circuit: dict        # the circuit as the user sent it (to report agent changes)
    circuit: dict
    checks: list
    stimulus: list
    code: str                 # the LLM's sketch, without the #define block
    full_code: str            # #define block + code: exactly what is compiled
    hex: str
    attempts: int             # fix cycles used
    history: list             # [{attempt, stage, problem, change_made}]
    problem: Optional[str]    # what went wrong in the last step (None = ok)
    problem_stage: Optional[str]
    fatal: bool               # provider or simulator error: retrying will not help
    unsupported: list         # part types the user asked for that we cannot simulate yet
    last_error_norm: Optional[str]
    repeat_error: bool
    behavior_summary: str
    report: dict              # evaluator output {passed, results}
    explanation: dict
    spoken_summary: str
    status: Optional[str]     # "passed" | "gave_up"
    steps: list               # [{step, ms, note}] for the progress display
    started_at: float
    debug_break: str          # "" | "compile" | "timing" (dev switch, see break_code)
    break_done: bool
    mode: str                 # "auto" (understand decides) | "build" | "chat"
    via: str                  # "text" | "voice" (shown in the chat)
    intent: str               # "build" | "chat" for this message
    reply: str                # chat answer (intent chat)
    reply_lines: str          # code lines the chat answer is about, e.g. "12-14"
    say: str                  # a short line to speak right away (Live mode): the ack, or what a fix is doing
    messages: list            # the conversation, kept across requests in the session


MAX_MESSAGES = 60            # kept in the checkpoint (older ones are dropped)
CONTEXT_MESSAGES = 8         # sent to the LLM as "recent conversation"


# ---------- prompts ----------

# How every text that Timbre reads aloud must be written (the voice picks its language from the script).
SPOKEN_RULES = ("1 or 2 short, friendly sentences that a text-to-speech voice will read aloud, in the SAME language as "
                "the user's message. Script rules: English in Latin letters; Hindi and Hinglish in Devanagari; Kannada "
                "and Kanglish in Kannada script. Common English device words (LED, servo, buzzer) may stay in Latin "
                "letters. Write every number as words in that language (e.g. \"pin nine\", \"ಒಂಬತ್ತನೇ ಪಿನ್\", \"पिन नौ\"). "
                "No code words (no millis, analogWrite, LED1_PIN), no symbols, and no words from other languages or scripts.")

CODE_RULES ="""Rules for the code:
- Write for the board described under "Board" in the request. No #include lines, except #include <Servo.h> when the circuit has a servo. No Serial.
- The backend adds one line per part: #define <ID>_PIN <pin> (for example LED1_PIN). Use only those names for pins. Never write a literal pin number or LED_BUILTIN. Do not write #define lines for pins yourself.
- analogWrite only on the board's PWM (~) pins, and follow the board notes (which timers the Servo library and tone() take over).
- Buttons: pinMode(<ID>_PIN, INPUT_PULLUP); pressed reads LOW. Potentiometers: analogRead(<ID>_PIN) gives 0 to 1023.
- A blink period is one full on+off cycle: for a 1000 ms period, the LED is on 500 ms and off 500 ms.
- Under 80 lines. Use millis() timing or short delay() steps. Define setup() and loop().
- Format it for a beginner to read: one statement per line, 2-space indentation, a few short comments."""

CHECKS_DOC = f"""Checks: the simulator runs the code for a few seconds and measures the pins. Write checks from the user's words (not from your code). Types:
- {{"type":"steady","target":ID,"level":"high" or "low"}}  (LED or buzzer stays on/off)
- {{"type":"blink","target":ID,"period_ms_min":N,"period_ms_max":N}}
- {{"type":"breathe","target":ID,"min_duty_below":0..1,"max_duty_above":0..1,"period_ms_min":N,"period_ms_max":N}}  (LED fades up and down)
- {{"type":"duty","target":ID,"min":0..1,"max":0..1}}  (LED brightness level, e.g. dim = 0.1..0.4)
- {{"type":"servo_angle","target":ID,"angle_deg":N,"tolerance_deg":N}}
- {{"type":"servo_sweep","target":ID,"min_angle_below":N,"max_angle_above":N}}  (servo moves back and forth)
- {{"type":"tone","target":ID,"freq_min":N,"freq_max":N}}  (buzzer plays a frequency in Hz)
Any check may add "from_ms" and/or "to_ms" to look only at that time window (default: from 200 ms to the end of the test).
Give every range a margin of about 25% on each side. Rough meanings: "fast" blink about 200 ms per cycle, "normal" about 1000 ms, "slow" about 2000 ms. "Slower" or "faster" without a number means about 2 times slower or faster than the current code. Keep blink and breathe periods at most 5000 ms so the test can see two cycles.

Stimulus simulates the user during the test: [{{"target":BUTTON_ID,"at_ms":N,"action":"press" or "release"}}, {{"target":POT_ID,"at_ms":N,"action":"set","value":0..1023}}], or [].
To test a reaction to a button: press it at 2000 ms, add a check with "to_ms":2000 for the behavior before the press, and a check with "from_ms":2300 for the behavior after it. If the behavior lasts only while the button is held, release it at 4000 ms and end the second check with "to_ms":4000.
A check's window must never contain a press, release or knob change. A blink or breathe check needs a window at least 2 periods long, so for slow behaviors press the button later (for a 4000 ms breathe: check it with "to_ms":8500, press at 8500, check the pressed behavior with "from_ms":8800). The test lasts at most {MAX_SIM_MS} ms.
Potentiometers start at the middle (512). To test a knob, set it at a few moments (e.g. 0 at 500 ms, 1023 at 4000 ms) and check the output in a window after each change (e.g. "from_ms":800,"to_ms":3900 and "from_ms":4300). Unless the user says otherwise, knob 0 means the minimum (0 degrees, off, lowest) and 1023 the maximum (180 degrees, full).
"""

PLAN_SYSTEM = f"""You write Arduino sketches that run in a simulator. Reply with JSON only.

{CODE_RULES}

The circuit:
- Use the user's circuit as given. Change it only if the user asks to add, move or remove a part.
- Part types: led, button, servo, buzzer, potentiometer. ids are uppercase, like LED1, BUTTON1, SERVO1, BUZZER1, POT1.
- Use only the board's pins: digital pins as numbers (e.g. 9), analog pins as strings (e.g. "A0"). Never 0 or 1. No two parts share a pin. Potentiometers go on analog inputs; other parts never on analog-only pins.
- If the user adds a part without naming a pin, pick a free compatible pin (a PWM pin if it needs analogWrite).
- If the request needs fading or brightness (analogWrite) on an LED that is on a non-PWM pin, move it to a free PWM pin and say so in behavior_summary (e.g. "moved LED1 to pin 9 because pin 13 cannot fade").
- If the request needs any hardware that is not one of the part types above (for example an LCD, OLED, relay, motor, NeoPixel, sensor or keypad), do NOT substitute or leave it out: reply {{"unsupported": ["lcd"]}} (the missing part names) with the usual keys left empty.

{CHECKS_DOC}

Reply format:
{{"circuit":{{"components":[{{"type":"led","id":"LED1","pin":13}}]}},"checks":[...],"stimulus":[...],"code":"...","behavior_summary":"one short English sentence"}}

The user may write in English, Hindi, Kannada, Hinglish or Kanglish. Understand all of them. Write code comments and behavior_summary in English.
The request may be a short follow-up ("slower", "yes, do that", "same but on pin 6"): read it together with the recent conversation."""

FIX_SYSTEM = f"""You fix an Arduino sketch that failed to compile or failed a measured check in the simulator. Reply with JSON only:
{{"code":"<the full corrected sketch, without the #define pin lines>","note":"one short sentence: what you changed and why","say":"..."}}
say: one short spoken sentence telling the user what the test showed and that you are fixing it, like "The first try breathed four times too slowly, so I'm speeding it up." ({SPOKEN_RULES})
Trust the measurements: they come from running your code. Fix the code so it does what the user asked.
For a timing problem, scale your delays and intervals by (wanted / measured): e.g. measured cycle 500 ms, wanted 2000 ms -> make every delay or interval 4 times longer. Aim for the middle of the expected range.
Add "checks" and "stimulus" only if the checks misread the user's request or the problem says a check itself is invalid (never just to match wrong code).
Add "circuit" ({{"components":[{{"type","id","pin"}}]}}) only if the problem is in the circuit itself.

{CODE_RULES}"""

FIX_CHECKS_SYSTEM = f"""The acceptance checks or stimulus for an Arduino simulator test are invalid. Rewrite them so they test what the user asked. Do not change the code. Reply with JSON only:
{{"checks":[...],"stimulus":[...],"note":"one short sentence: what you changed and why"}}

{CHECKS_DOC}"""

SUPPORTED_TEXT = ("Parts the simulator can use: LED, push button, servo, buzzer, potentiometer. "
                  "Boards: Arduino Uno, Nano and Mega 2560.")

WRITTEN_LANGUAGE = ("Write it in the language and script of the user's message: English stays English; Hinglish or "
                    "Kanglish typed in Latin letters stays in Latin letters (never switch it to Devanagari or Kannada "
                    "script); Hindi in Devanagari stays Devanagari; Kannada in Kannada script stays Kannada script. "
                    "Write numbers as digits (pin 13, 2000 ms). Code, function and pin names stay exactly as written.")

EXPLAIN_SYSTEM = f"""You explain a working Arduino sketch to a beginner. Reply with JSON only, like this example for an LED on pin 13:
{{"summary":"The LED on pin 13 blinks once a second. millis() times it, so loop() never waits.","lines":[{{"line":3,"note":"LED1_PIN means pin 13, where the LED is connected."}}],"try_next":["make it blink faster","add a button on pin 2"],"spoken_summary":"The LED on pin thirteen now blinks once every second."}}
- lines: one entry for EVERY line number listed under "Lines to explain", in order, with that number. note: one short sentence (at most about 15 words) on what that line does in this sketch, with the real values (pin 9, 500 ms, 90 degrees). For a line that starts a function or block (void loop() {{, if (...) {{), say when it runs. Say what functions like pinMode, digitalWrite, analogWrite, analogRead, delay, millis, tone or Servo do.
- summary and notes: {WRITTEN_LANGUAGE} Only spoken_summary spells numbers out as words.
- try_next: 2 or 3 short follow-up requests the user could say next, like "make it blink slower".
- spoken_summary: {SPOKEN_RULES} Say what the hardware now does. Use exactly the pin numbers in "Parts" (never a pin from try_next or from the example)."""

CONVERSE_SYSTEM = f"""You are the assistant in a chat inside an Arduino simulator. The user builds a circuit on screen and talks to you by voice or text, in English, Hindi, Kannada, Hinglish or Kanglish. A separate builder writes, compiles, runs and measures the code. Decide whether the latest message needs the builder, and if not, answer it yourself. Reply with JSON only.

If the user wants the hardware or the code to change in any way, reply {{"intent":"build","ack":"..."}}. ack: one short, friendly spoken sentence saying what you are about to do, like "Sure, I'll make the LED breathe slowly. Give me a few seconds." ({SPOKEN_RULES}). A change request includes new behavior, faster or slower, adding, moving or removing a part, "do it again", "yes, do that" after you offered a change, and short follow-ups like "slower", "brighter", "on pin 6", "can you make it blink?".

Otherwise reply {{"intent":"chat","reply":"...","lines":"...","spoken":"..."}}. That covers questions about the current code, circuit, wiring, test results or fixes ("explain line 7", "why millis and not delay?", "what happens when I press the button?"), general Arduino and electronics questions, greetings, thanks and "what can you do?".
- reply: answer from the code, circuit and test results given. Refer to code as "line 12" or "lines 12-14" (the user's editor shows these numbers). Be brief: 2 to 6 short sentences or a short list of "- " lines, unless the user asks for detail (e.g. "explain everything line by line"). Put code in `backticks`. Never claim a measurement that is not in the test results. Do not write a whole new sketch here. If you are not sure whether the user wants a change, say what you would change and ask them to confirm.
- reply language: {WRITTEN_LANGUAGE} Earlier messages may be in other languages: follow the latest message only.
- {SUPPORTED_TEXT} If asked about anything else, say honestly that it cannot be simulated yet.
- lines: the code lines your answer is mainly about ("7" or "12-14"), or "" if none.
- spoken: {SPOKEN_RULES} An English message gets an English spoken line."""

# Characters from scripts the speech output should never contain (CJK, Japanese, Korean, Cyrillic, Arabic).
FOREIGN_SCRIPT = re.compile(r"[Ѐ-ӿ؀-ۿ぀-ヿ㐀-䶿一-鿿가-힯]+")


def clean_spoken(text):
    """Safety net: drop stray foreign-script words some models slip in, and tidy spaces."""
    return re.sub(r"\s{2,}", " ", FOREIGN_SCRIPT.sub(" ", text or "")).strip()


# ---------- helpers ----------

def elapsed_ms(state):
    return round((time.time() - state["started_at"]) * 1000)


def add_step(state, step, note=""):
    return state.get("steps", []) + [{"step": step, "ms": elapsed_ms(state), "note": note}]


def normalize_pin(pin, board):
    """13, "13" -> 13;  "a0", "A0" -> "A0". Returns None if not a pin of this board."""
    text = str(pin).strip().upper()
    if text not in board["pins"]:
        return None
    return int(text) if text.isdigit() else text


def define_block(circuit):
    lines = ["// Pins from your circuit (written by the backend)"]
    comps = circuit.get("components") if isinstance(circuit, dict) else None
    lines += [f"#define {c.get('id')}_PIN {c.get('pin')}" for c in (comps or []) if isinstance(c, dict)]
    return "\n".join(lines) + "\n\n"


def strip_pin_defines(code):
    """The backend owns the pin numbers, so drop any #define X_PIN lines the LLM wrote."""
    return re.sub(r"^[ \t]*#define\s+\w+_PIN\b.*\n?", "", code, flags=re.M)


def break_code(code, mode="compile"):
    """Dev switch for testing and the demo.
    compile: remove the first statement-ending semicolon so compiling fails.
    timing:  make every delay() and timing constant 4x longer, so compiling works
             but the measured behavior fails its check and the agent must fix it from the numbers."""
    if mode == "timing":
        timing_name = r"\w*(interval|period|delay|wait|duration|time|ms)\w*"
        slowed, n1 = re.subn(r"\bdelay\s*\(\s*(\d+)\s*\)", lambda m: f"delay({int(m.group(1)) * 4})", code)
        slowed, n2 = re.subn(rf"(\b{timing_name}\s*=\s*)(\d+)(UL|L)?\b",
                             lambda m: f"{m.group(1)}{int(m.group(3)) * 4}{m.group(4) or ''}", slowed, flags=re.I)
        if n1 + n2:
            return slowed
    broken, n = re.subn(r";([ \t]*(//[^\n]*)?\n)", r"\1", code, count=1)
    return broken if n else "int broken_on_purpose = ;\n" + code


def circuit_changes(old, new):
    """Plain sentences for every difference between the user's circuit and the agent's."""
    before = {c["id"]: c for c in (old or {}).get("components", [])}
    after = {c["id"]: c for c in (new or {}).get("components", []) if isinstance(c, dict)}
    out = []
    for cid, c in after.items():
        if cid not in before:
            out.append(f"added {cid} ({c.get('type')}) on pin {c.get('pin')}")
        elif str(before[cid]["pin"]) != str(c.get("pin")):
            out.append(f"moved {cid} from pin {before[cid]['pin']} to pin {c.get('pin')}")
    out += [f"removed {cid}" for cid in before if cid not in after]
    return out


def numbered(code):
    return "\n".join(f"{i:3d}| {line}" for i, line in enumerate(code.splitlines(), 1))


def explain_line_numbers(code):
    """Line numbers worth a note: not blank, not only braces/brackets, not a comment-only line."""
    out = []
    for i, line in enumerate(code.splitlines(), 1):
        text = line.strip()
        if not text or re.fullmatch(r"[{}()\[\];,\s]*", text) or text.startswith(("//", "/*", "*")):
            continue
        out.append(i)
    return out


def line_by_line(code, notes):
    """[{line, code, note}] for every explainable line. The code text comes from the sketch itself
    (never from the LLM); notes is the LLM's [{line, note}] list (missing notes stay empty)."""
    by_line = {}
    for n in notes if isinstance(notes, list) else []:
        if isinstance(n, dict) and isinstance(n.get("line"), int) and isinstance(n.get("note"), str):
            by_line.setdefault(n["line"], n["note"].strip())
    lines = code.splitlines()
    return [{"line": i, "code": lines[i - 1].strip(), "note": by_line.get(i, "")} for i in explain_line_numbers(code)]


def script_hint(text):
    """Which alphabet the user typed in, so the model answers in the same one (it tends to switch
    Latin-letter Hinglish to Devanagari otherwise)."""
    # Code-mixed text (Prisma's "codemix" transcripts) keeps English device words in Latin letters,
    # so any real Devanagari or Kannada text decides the script, even when Latin letters outnumber it.
    native = {"Devanagari": len(re.findall(r"[ऀ-ॿ]", text or "")),
              "Kannada": len(re.findall(r"[ಀ-೿]", text or ""))}
    script = max(native, key=native.get)
    if native[script] < 2:
        return "The user's message is in Latin letters (English, or Hinglish/Kanglish typed in Latin letters): write in Latin letters."
    return f"The user's message is in {script} script (English device words may stay in Latin letters): write in {script} script."


def conversation_text(messages, limit=CONTEXT_MESSAGES):
    """The last few chat messages as plain text for a prompt (long ones cut short)."""
    rows = []
    for m in (messages or [])[-limit:]:
        who = "User" if m.get("role") == "user" else "Assistant"
        text = re.sub(r"\s+", " ", m.get("text") or "").strip()
        if m.get("kind") == "build":
            text = f"[{'built and verified' if m.get('status') == 'passed' else 'could not build'}] {text}"
        rows.append(f"{who}: {text[:400]}")
    return "\n".join(rows)


def test_results_text(state):
    """The last build's measured results and fixes, for answering questions about them."""
    results = (state.get("report") or {}).get("results") or []
    if not results:
        return "Last test results: none yet."
    lines = [f"- {'passed' if r['ok'] else 'FAILED'}: {r['detail']}" for r in results]
    fixes = [f"- attempt {h['attempt']} ({h['stage']}): {h['change_made']}" for h in state.get("history") or []]
    return ("Last test results (measured in the simulator):\n" + "\n".join(lines)
            + ("\nFixes made during that build:\n" + "\n".join(fixes) if fixes else ""))


def append_messages(state, *new):
    return ((state.get("messages") or []) + list(new))[-MAX_MESSAGES:]


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ---------- preflight checks (pure Python) ----------

def check_circuit(circuit, board):
    comps = circuit.get("components") if isinstance(circuit, dict) else None
    if not isinstance(comps, list):
        return "circuit must be {\"components\": [...]}"
    ids, pins = set(), set()
    for c in comps:
        if not isinstance(c, dict):
            return "each component must be an object with type, id and pin"
        if c.get("type") not in COMPONENT_TYPES:
            return f"component type '{c.get('type')}' is not allowed (use {sorted(COMPONENT_TYPES)})"
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,15}", str(c.get("id", ""))):
            return f"component id '{c.get('id')}' must be uppercase like LED1"
        pin = normalize_pin(c.get("pin"), board)
        if pin is None:
            return f"{c['id']} has pin '{c.get('pin')}', which is not a pin of the {board['name']}"
        info = board["pins"][str(pin)]
        if info.get("reserved"):
            return f"{c['id']} is on pin {pin}, which is reserved ({info['reserved']})"
        if c["type"] == "potentiometer" and not info.get("analog"):
            return f"{c['id']} is a potentiometer, so it must be on an analog input ({', '.join(analog_pins(board))})"
        if c["type"] != "potentiometer" and info.get("analog_only"):
            return f"{c['id']} is on {pin}, which can only read analog values; use a digital pin"
        if c["id"] in ids:
            return f"two components are called {c['id']}"
        if pin in pins:
            return f"pin {pin} is used by two components"
        c["pin"] = pin
        ids.add(c["id"])
        pins.add(pin)
    return None


def widen(check, lo_key, hi_key, margin=0.15):
    """Measurements are never exact: make sure a range is at least +-15% around its middle."""
    lo, hi = check[lo_key], check[hi_key]
    mid = (lo + hi) / 2
    check[lo_key] = min(lo, mid * (1 - margin))
    check[hi_key] = max(hi, mid * (1 + margin))


def make_measurable(ch):
    """Measurement tolerance, applied by the backend (not the LLM): no exact or impossible targets.
    A PWM LED never measures exactly 0% or 100%, and a servo sweep is never exactly 0 to 180."""
    t = ch["type"]
    if "period_ms_min" in ch:
        widen(ch, "period_ms_min", "period_ms_max")
    if t == "tone":
        widen(ch, "freq_min", "freq_max", 0.05)
    if t == "breathe":
        ch["min_duty_below"] = min(max(ch["min_duty_below"], 0.1), 0.5)
        ch["max_duty_above"] = max(min(ch["max_duty_above"], 0.9), 0.5)
    if t == "duty":
        ch["min"], ch["max"] = max(0.0, ch["min"] - 0.05), min(1.0, ch["max"] + 0.05)
    if t == "servo_sweep":
        ch["min_angle_below"] = max(ch["min_angle_below"], 15)
        ch["max_angle_above"] = min(ch["max_angle_above"], 165)
    if t == "servo_angle":
        ch["tolerance_deg"] = max(ch.get("tolerance_deg", 5), 5)


def check_checks(checks, stimulus, circuit):
    types = {c["id"]: c["type"] for c in circuit["components"]}
    if not isinstance(checks, list) or not isinstance(stimulus, list):
        return "checks and stimulus must be lists"
    if not checks:
        return "add at least one check describing what the user asked for"
    for ch in checks:
        if not isinstance(ch, dict) or ch.get("type") not in CHECKS:
            return f"check {ch} has a type that is not allowed (use {sorted(CHECKS)})"
        required, targets = CHECKS[ch["type"]]
        missing = required - ch.keys()
        if missing:
            return f"check {ch['type']} is missing {sorted(missing)}"
        if ch["target"] not in types:
            return f"check {ch['type']} targets {ch['target']}, which is not in the circuit"
        if types[ch["target"]] not in targets:
            return f"check {ch['type']} cannot target {ch['target']} (a {types[ch['target']]})"
        for key in required - {"target", "level"} | ({"after_ms", "before_ms", "from_ms", "to_ms", "tolerance_deg"} & ch.keys()):
            if not is_number(ch[key]):
                return f"check {ch['type']}: {key} must be a number"
        # Time window: from_ms/to_ms (after_ms/before_ms accepted as old names).
        if "after_ms" in ch:
            ch.setdefault("from_ms", ch.pop("after_ms"))
        if "before_ms" in ch:
            ch.setdefault("to_ms", ch.pop("before_ms"))
        a, b = ch.get("from_ms"), ch.get("to_ms")
        if (a is not None and not 0 <= a <= MAX_SIM_MS - 1000) or (b is not None and not 500 <= b <= MAX_SIM_MS) \
                or (a is not None and b is not None and b - a < 500):
            return (f"check {ch['type']} has the time window from_ms={a} to_ms={b}; it must satisfy "
                    f"0 <= from_ms < to_ms <= {MAX_SIM_MS}, at least 500 ms long. Leave out from_ms to start at "
                    f"the beginning, or to_ms to run to the end")
        # A repeating behavior can only be measured if the window holds about two full cycles.
        if ch["type"] in ("blink", "breathe"):
            span = (b if b is not None else MAX_SIM_MS) - (a if a is not None else 200)
            need = 2.2 * ch["period_ms_max"]
            if span < need:
                return (f"check {ch['type']} {ch['target']} looks at only {span:.0f} ms, but a period of up to "
                        f"{ch['period_ms_max']:.0f} ms needs a window of at least {need:.0f} ms (about two cycles). "
                        f"Make the window longer, e.g. move the button press later")
        if ch["type"] == "steady" and ch["level"] not in ("high", "low"):
            return "check steady: level must be \"high\" or \"low\""
        for lo, hi in (("period_ms_min", "period_ms_max"), ("freq_min", "freq_max"), ("min", "max")):
            if lo in ch and ch[lo] > ch[hi]:
                return f"check {ch['type']}: {lo} is larger than {hi}"
        make_measurable(ch)
    for s in stimulus:
        if not isinstance(s, dict) or s.get("target") not in types or not is_number(s.get("at_ms")):
            return f"stimulus {s} must have a target in the circuit and a number at_ms"
        kind = types[s["target"]]
        if kind == "button" and s.get("action") not in ("press", "release"):
            return f"stimulus for {s['target']} must use action press or release"
        if kind == "potentiometer" and (s.get("action") != "set" or not is_number(s.get("value"))):
            return f"stimulus for {s['target']} must be action set with a value 0 to 1023"
        if kind not in ("button", "potentiometer"):
            return f"stimulus can only drive buttons and potentiometers, not {s['target']}"
    # A check must measure one behavior: its window may not contain a press, release or knob change.
    for ch in checks:
        a, b = ch.get("from_ms", 200), ch.get("to_ms", MAX_SIM_MS)
        inside = [s for s in stimulus if a + 50 < s["at_ms"] < b - 50]
        if inside:
            s = inside[0]
            return (f"check {ch['type']} {ch['target']} covers {a}-{b} ms, but {s['target']} is "
                    f"{s['action']}{'ed' if s['action'] != 'set' else ''} at {s['at_ms']} ms inside that window. "
                    f"Give each check a from_ms/to_ms window that does not contain a stimulus "
                    f"(one check before it, another after it)")
    return None


def check_code(code, circuit, board):
    if not isinstance(code, str) or not code.strip():
        return "code is empty"
    if code.count("\n") + 1 > MAX_CODE_LINES or len(code.encode()) > MAX_CODE_BYTES:
        return f"code is too long (limit {MAX_CODE_LINES} lines / {MAX_CODE_BYTES} bytes)"
    for header in re.findall(r"#include\s*[<\"]([^>\"]+)[>\"]", code):
        if header not in ("Servo.h", "Arduino.h"):  # Arduino.h is harmless (already included)
            return f"#include <{header}> is not allowed; only <Servo.h> when there is a servo"
    if re.search(r"\bSerial\d?\s*\.", code):
        return "Serial is not allowed in this simulator; remove it"
    if "LED_BUILTIN" in code:
        return "use the <ID>_PIN names instead of LED_BUILTIN"
    m = re.search(r"\b(pinMode|digitalWrite|digitalRead|analogWrite|analogRead|tone|noTone)\s*\(\s*(A?\d+)\b", code) \
        or re.search(r"\.(attach)\s*\(\s*(A?\d+)\b", code)
    if m:
        return f"{m.group(1)}({m.group(2)}, ...) uses a literal pin number; use the <ID>_PIN names"

    by_id = {c["id"]: c for c in circuit["components"]}
    for name in set(re.findall(r"\b([A-Z][A-Z0-9_]*)_PIN\b", code)):
        if name not in by_id:
            return f"the code uses {name}_PIN but the circuit has no part called {name}"
    has_servo = "Servo" in code
    has_tone = re.search(r"\btone\s*\(", code) is not None
    for name in set(re.findall(r"\banalogWrite\s*\(\s*([A-Z][A-Z0-9_]*)_PIN\b", code)):
        pin = by_id[name]["pin"]
        if pin not in pwm_pins(board):
            return f"analogWrite on {name} (pin {pin}), which is not PWM-capable; PWM pins are {pwm_pins(board)}"
        if has_servo and pin in board["servo_blocks_pwm"]:
            return f"analogWrite on {name} (pin {pin}) does not work while the Servo library is used"
        if has_tone and pin in board["tone_blocks_pwm"]:
            return f"analogWrite on {name} (pin {pin}) does not work while tone() is used"
    return None


# ---------- verification setup ----------

def pin_ref(pin, board):
    info = board["pins"][str(pin)]
    return {"pin": pin, "port": info["port"], "bit": info["bit"]}


def sim_config(circuit, checks, stimulus, board):
    """Turn component ids into pins and build the verify.mjs config."""
    comps = circuit["components"]
    by_id = {c["id"]: c for c in comps}
    resolved = [{**c, "pin": by_id[c["target"]]["pin"]} for c in checks]

    duration = MIN_SIM_MS
    for c in resolved:
        start = c.get("from_ms", 200)
        if "period_ms_max" in c and "to_ms" not in c:  # see at least two full cycles after the window starts
            duration = max(duration, start + 2.5 * c["period_ms_max"])
        duration = max(duration, start + 1500, c.get("to_ms", 0))
    for s in stimulus:
        duration = max(duration, s["at_ms"] + 1500)
    duration = int(min(duration, MAX_SIM_MS))

    adc = lambda pin: board["pins"][str(pin)]["adc"]  # analog channel (A6/A7 have no digital port)
    events = []
    for s in stimulus:
        part = by_id[s["target"]]
        if part["type"] == "button":  # pressed pulls the pin LOW
            ref = pin_ref(part["pin"], board)
            events.append({"at_ms": s["at_ms"], "kind": "pin", "port": ref["port"], "bit": ref["bit"],
                           "level": s["action"] == "release"})
        else:
            events.append({"at_ms": s["at_ms"], "kind": "analog", "channel": adc(part["pin"]),
                           "volts": max(0, min(1023, s["value"])) / 1023 * 5})
    config = {
        "mcu": board["mcu"],
        "duration_ms": duration,
        "watch": [pin_ref(c["pin"], board) for c in comps if c["type"] in OUTPUT_TYPES],
        "inputs": [{**pin_ref(c["pin"], board), "level": True} for c in comps if c["type"] == "button"],
        "analog": [{"channel": adc(c["pin"]), "volts": 2.5} for c in comps if c["type"] == "potentiometer"],
        "events": events,
    }
    return config, resolved


def run_simulation(hex_text, config):
    """Run verify.mjs. Returns (measurements, None) or (None, error text)."""
    node = shutil.which("node")
    if not node:
        return None, "node was not found on PATH"
    with tempfile.TemporaryDirectory(prefix="gnani_sim_") as tmp:
        hex_file, cfg_file = Path(tmp) / "sketch.hex", Path(tmp) / "config.json"
        hex_file.write_text(hex_text)
        cfg_file.write_text(json.dumps(config))
        try:
            out = subprocess.run([node, str(VERIFY_SCRIPT), str(hex_file), str(cfg_file)],
                                 capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return None, "the simulation took longer than 30 s"
    if out.returncode != 0:
        return None, f"simulator error: {out.stderr.strip()[:500]}"
    return json.loads(out.stdout), None


# ---------- nodes ----------

def understand(state):
    """First stop for every message: add it to the conversation, then decide build or chat.
    A chat question is answered right here (one LLM call); a build request goes to plan_and_code."""
    user_msg = {"role": "user", "text": state["user_text"], "via": state.get("via") or "text"}
    update = {"messages": append_messages(state, user_msg), "reply": "", "reply_lines": ""}
    mode = state.get("mode") or "auto"
    if mode == "build":  # e.g. a "Try next" chip: no need to ask the model
        return {**update, "intent": "build"}

    code = state.get("full_code") or ""
    parts = ", ".join(f"{c['id']} ({c['type']}) on pin {c['pin']}" for c in state["circuit"].get("components", [])) or "none"
    user = (
        f"{board_facts(get_board(state.get('board')))}\n"
        f"Parts on screen: {parts}\n\n"
        + (f"Current code (the numbers match the user's editor):\n{numbered(code)}\n\n" if code
           else "Current code: none yet (nothing has been built in this session).\n\n")
        + f"{test_results_text(state)}\n\n"
        f"Recent conversation:\n{conversation_text(state.get('messages')) or '(none)'}\n\n"
        f"Latest message: {state['user_text']}\n"
        f"{script_hint(state['user_text'])}"
        + ("\n\nThe user wants an answer, not a change: reply with intent chat." if mode == "chat" else "")
    )
    try:
        out = chat_json(CONVERSE_SYSTEM, user, required_keys=("intent",))
    except LLMError:
        # Could not decide: treat it as a build request, which handles provider errors honestly.
        return {**update, "intent": "build", "steps": add_step(state, "understanding", "treating it as a build request")}
    reply = str(out.get("reply") or "").strip()
    if out.get("intent") != "chat" or not reply:
        return {**update, "intent": "build", "say": clean_spoken(str(out.get("ack") or "")),
                "steps": add_step(state, "understanding", "a request to build")}
    lines = str(out.get("lines") or "").strip()
    return {**update, "intent": "chat", "reply": reply,
            "reply_lines": lines if re.fullmatch(r"\d+(\s*-\s*\d+)?", lines) else "",
            "spoken_summary": clean_spoken(str(out.get("spoken") or "")),
            "steps": add_step(state, "understanding", "a question, answered")}


# Fields one build starts fresh with. They are not reset per request, so a chat question after
# a build can still talk about its code, test results and fixes.
BUILD_RESET = {"attempts": 0, "history": [], "hex": "", "full_code": "", "report": {}, "explanation": {},
               "spoken_summary": "", "last_error_norm": None, "repeat_error": False, "behavior_summary": ""}


def plan_and_code(state):
    previous = state.get("code") or ""
    convo = conversation_text((state.get("messages") or [])[:-1], limit=6)  # the request itself is below
    user = (
        f"Request: {state['user_text']}\n\n"
        + (f"Recent conversation (to understand the request):\n{convo}\n\n" if convo else "")
        + f"{board_facts(get_board(state.get('board')))}\n\n"
        f"Circuit: {json.dumps(state['circuit'])}\n\n"
        + (f"Current code (change only what the request asks for):\n{previous}" if previous
           else "Current code: none, write a new sketch.")
    )
    try:
        out = chat_json(PLAN_SYSTEM, user, required_keys=("circuit", "checks", "code"))
    except LLMError as e:
        return {**BUILD_RESET, "problem": f"planning failed: {e}", "problem_stage": "plan", "fatal": not e.bad_output,
                "steps": add_step(state, "planning", "failed")}
    if out.get("unsupported"):
        names = [str(x) for x in out["unsupported"]] if isinstance(out["unsupported"], list) else [str(out["unsupported"])]
        return {**BUILD_RESET, "problem": f"unsupported part: {', '.join(names)}", "problem_stage": "plan", "fatal": True,
                "unsupported": names, "steps": add_step(state, "planning", f"needs a part I can't simulate yet: {', '.join(names)}")}
    note = out.get("behavior_summary", "")
    changes = circuit_changes(state.get("user_circuit"), out["circuit"])
    if changes:
        note += " | Wiring: " + "; ".join(changes)
    return {
        **BUILD_RESET,
        "circuit": out["circuit"],
        "checks": out.get("checks", []),
        "stimulus": out.get("stimulus", []),
        "code": strip_pin_defines(out.get("code") or ""),
        "behavior_summary": out.get("behavior_summary", ""),
        "problem": None,
        "steps": add_step(state, "planning", note),
    }


def move_to_pwm(circuit, code, checks, board):
    """Fading needs a PWM (~) pin. If an LED that must fade sits on another pin, move it to a free
    PWM pin. The code only uses LED1_PIN-style names, so it needs no change. Returns the moves."""
    needs_pwm = {c.get("target") for c in checks if c.get("type") in ("breathe", "duty")}
    needs_pwm |= set(re.findall(r"\banalogWrite\s*\(\s*([A-Z][A-Z0-9_]*)_PIN\b", code))
    blocked = set()
    if "Servo" in code:
        blocked |= set(board["servo_blocks_pwm"])
    if re.search(r"\btone\s*\(", code):
        blocked |= set(board["tone_blocks_pwm"])
    # Prefer the classic PWM pins first (9, 10, 11 ...), then any other PWM pin of the board.
    candidates = [p for p in (9, 10, 11, 5, 6, 3) if p in pwm_pins(board)] + pwm_pins(board)
    used = {c["pin"] for c in circuit["components"]}
    moves = []
    for c in circuit["components"]:
        if c["id"] in needs_pwm and c["type"] == "led" and c["pin"] not in pwm_pins(board):
            free = next((p for p in candidates if p not in used and p not in blocked), None)
            if free is not None:
                moves.append(f"moved {c['id']} from pin {c['pin']} to pin {free} (pin {c['pin']} cannot fade; ~ pins can)")
                used.discard(c["pin"])
                used.add(free)
                c["pin"] = free
    return moves


def preflight(state):
    circuit, board = state["circuit"], get_board(state.get("board"))
    checks, stimulus = state.get("checks", []), state.get("stimulus", [])
    problem = check_circuit(circuit, board)
    moves = [] if problem else move_to_pwm(circuit, state["code"] or "", checks, board)
    problem = (problem
               or check_checks(checks, stimulus, circuit)
               or check_code(state["code"], circuit, board))
    note = problem or ("ok, " + "; ".join(moves) if moves else "ok")
    result = {"steps": add_step(state, "preflight", note), "problem": problem,
              "problem_stage": "preflight" if problem else None}
    # A part we cannot simulate yet is not something a fix can solve (and must never be quietly dropped):
    # unknown part types, or code that pulls in a library for unsupported hardware, stop the run honestly.
    comps = circuit.get("components") if isinstance(circuit, dict) else None
    unknown = {str(c.get("type")) for c in (comps or []) if isinstance(c, dict) and c.get("type") not in COMPONENT_TYPES}
    for header in re.findall(r"#include\s*[<\"]([^>\"]+)[>\"]", state.get("code") or ""):
        for key, part in HARDWARE_LIBRARIES.items():
            if key.lower() in header.lower():
                unknown.add(part)
    if unknown:
        result.update(fatal=True, unsupported=sorted(unknown))
    if not problem:
        # pins normalized and ranges widened in place
        result.update(circuit=circuit, checks=checks, full_code=define_block(circuit) + state["code"])
    return result


def compile_node(state):
    attempt = state.get("attempts", 0) + 1
    code, full_code, update = state["code"], state["full_code"], {}
    if state.get("debug_break") and not state.get("break_done"):
        code = break_code(code, state["debug_break"])
        full_code = define_block(state["circuit"]) + code
        update = {"code": code, "full_code": full_code, "break_done": True}

    offset = define_block(state["circuit"]).count("\n")
    result = compile_sketch(full_code, line_offset=offset, fqbn=get_board(state.get("board"))["fqbn"])
    if result["ok"]:
        return {**update, "hex": result["hex"], "problem": None,
                "steps": add_step(state, "compiling", f"attempt {attempt}: ok")}

    first_error = next((l for l in result["error"].splitlines() if "error" in l), "error")
    norm = normalize_error(result["error"])
    return {**update, "problem": result["error"], "problem_stage": "compile",
            "repeat_error": norm == state.get("last_error_norm"), "last_error_norm": norm,
            "steps": add_step(state, "compiling", f"attempt {attempt}: {first_error[:120]}")}


def verify(state):
    config, resolved = sim_config(state["circuit"], state["checks"], state.get("stimulus", []),
                                  get_board(state.get("board")))
    measurements, error = run_simulation(state["hex"], config)
    if error:
        return {"problem": error, "problem_stage": "verify", "fatal": True,
                "steps": add_step(state, "verifying", error)}
    report = evaluate(resolved, measurements)
    passed = [r for r in report["results"] if r["ok"]]
    failed = [r for r in report["results"] if not r["ok"]]
    note = (f"{len(passed)}/{len(report['results'])} checks passed in {config['duration_ms'] / 1000:.1f} s "
            f"of simulated time")
    if report["passed"]:
        return {"report": report, "problem": None, "steps": add_step(state, "verifying", note)}
    # Same failure as last time (numbers ignored) -> tell fix to rewrite instead of patching.
    norm = normalize_error(" ".join(r["detail"] for r in failed))
    problem = ("The code compiled, but running it in the simulator did not do what the user asked.\n"
               "Measured results:\n" + "\n".join(f"- FAILED {r['check']}: {r['detail']}" for r in failed)
               + "".join(f"\n- passed {r['check']}: {r['detail']}" for r in passed)
               + f"\nChecks: {json.dumps(state['checks'])}\nStimulus: {json.dumps(state.get('stimulus', []))}")
    return {"report": report, "problem": problem, "problem_stage": "verify",
            "repeat_error": norm == state.get("last_error_norm"), "last_error_norm": norm,
            "steps": add_step(state, "verifying", note + ": " + failed[0]["detail"])}


def fix(state):
    attempt = state.get("attempts", 0) + 1
    history_text = "\n".join(
        f"- attempt {h['attempt']} ({h['stage']}): {h['problem'][:300]} -> changed: {h['change_made']}"
        for h in state.get("history", [])) or "none"
    user = (
        f"Request: {state['user_text']}\n"
        f"{script_hint(state['user_text'])} (for say)\n\n"
        f"{board_facts(get_board(state.get('board')))}\n\n"
        f"Circuit: {json.dumps(state['circuit'])}\n\n"
        f"Pin defines added by the backend:\n{define_block(state['circuit'])}"
        f"Code (line numbers in errors refer to this code):\n{state.get('code') or '(none yet)'}\n\n"
        f"Problem ({state.get('problem_stage')}):\n{state['problem']}\n\n"
        f"Earlier fix attempts:\n{history_text}"
    )
    checks_wrong = state.get("problem_stage") == "preflight" and re.match(r"(check|stimulus|add at least)", state["problem"])
    if checks_wrong:
        # The code is not the problem: ask only for new checks and stimulus.
        system, required = FIX_CHECKS_SYSTEM, ("checks", "stimulus")
        user += (f"\n\nCurrent checks: {json.dumps(state.get('checks', []))}"
                 f"\nCurrent stimulus: {json.dumps(state.get('stimulus', []))}")
    else:
        system, required = FIX_SYSTEM, ("code",)
        if state.get("repeat_error"):
            user += "\n\nThe same error happened twice in a row. Rewrite the sketch from scratch, more simply, instead of patching."

    entry = {"attempt": attempt, "stage": state.get("problem_stage"), "problem": state["problem"]}
    try:
        out = chat_json(system, user, required_keys=required)
    except LLMError as e:
        entry["change_made"] = f"fix failed: {e}"
        return {"attempts": attempt, "history": state.get("history", []) + [entry],
                "fatal": not e.bad_output, "problem": f"fix failed: {e}", "problem_stage": "fix",
                "steps": add_step(state, "fixing", "failed")}

    note = out.get("note", "")
    update = {"problem": None}
    if not checks_wrong:
        update["code"] = strip_pin_defines(out["code"])
    # Speak only about failures the user would understand (a measurement or a compile error);
    # rewriting the test's own checks is internal.
    if state.get("problem_stage") in ("verify", "compile") and out.get("say"):
        update["say"] = clean_spoken(str(out["say"]))
    for key, kind in (("checks", list), ("stimulus", list), ("circuit", dict)):
        if isinstance(out.get(key), kind):
            update[key] = out[key]
            if not checks_wrong:
                note += f" (also changed the {key})"
    entry["change_made"] = note
    return {**update, "attempts": attempt, "history": state.get("history", []) + [entry],
            "steps": add_step(state, "fixing", note)}


def explain(state):
    results = state["report"]["results"]
    parts = ", ".join(f"{c['id']} ({c['type']}) on pin {c['pin']}" for c in state["circuit"]["components"])
    changes = circuit_changes(state.get("user_circuit"), state["circuit"])
    user = (
        f"User's request: {state['user_text']}\n"
        f"{script_hint(state['user_text'])} (summary and notes)\n"
        f"Parts: {parts}\n"
        + (f"Wiring changed by the agent (mention this in summary and spoken_summary, with the reason): "
           f"{'; '.join(changes)}\n" if changes else "")
        +
        f"What the code does: {state.get('behavior_summary', '')}\n"
        f"Verified in the simulator: {'; '.join(r['detail'] for r in results)}\n"
        f"Fixes made on the way: {'; '.join(h['change_made'] for h in state.get('history', [])) or 'none'}\n\n"
        f"Code with line numbers:\n{numbered(state['full_code'])}\n\n"
        f"Lines to explain: {', '.join(map(str, explain_line_numbers(state['full_code'])))}"
    )
    try:
        out = chat_json(EXPLAIN_SYSTEM, user, required_keys=("summary", "lines", "spoken_summary"))
    except LLMError as e:
        # The code works and is verified; a missing explanation should not hide that.
        out = {"summary": state.get("behavior_summary", ""), "lines": [], "try_next": [],
               "spoken_summary": state.get("behavior_summary", ""), "error": str(e)}
    explanation = {
        "summary": out.get("summary", ""),
        "lines": line_by_line(state["full_code"], out.get("lines")),
        "try_next": [str(t) for t in out.get("try_next", [])][:3] if isinstance(out.get("try_next"), list) else [],
        # Added by the backend, never written by the LLM:
        "verified": results,
        "fixes_made": [{"attempt": h["attempt"], "stage": h["stage"], "change": h["change_made"]}
                       for h in state.get("history", [])],
    }
    note = "done" if "error" not in out else f"LLM failed ({out['error'][:120]}), short version"
    return {"explanation": explanation, "spoken_summary": clean_spoken(out.get("spoken_summary", "")), "status": "passed",
            "steps": add_step(state, "explaining", note)}


PART_NAMES = {"led": "LEDs", "button": "push buttons", "servo": "servos", "buzzer": "buzzers",
              "potentiometer": "potentiometers"}
# Library header (substring) -> the hardware it means. Used to refuse honestly instead of dropping the part.
HARDWARE_LIBRARIES = {"LiquidCrystal": "an LCD", "SSD1306": "an OLED display", "U8g2": "an OLED display",
                      "NeoPixel": "NeoPixel LEDs", "FastLED": "NeoPixel LEDs", "DHT": "a DHT sensor",
                      "Stepper": "a stepper motor", "Keypad": "a keypad", "IRremote": "an IR remote",
                      "TM1637": "a 4-digit display", "MPU6050": "an MPU6050 sensor", "RTClib": "a clock module",
                      "HX711": "a load cell", "Wire.h": "an I2C device", "SPI.h": "an SPI device"}


def give_up(state):
    if state.get("unsupported"):
        def readable(name):  # "lcd" -> "an LCD", "relay" -> "a relay", "an OLED display" stays
            name = name.strip()
            if re.match(r"(a|an) ", name):
                return name
            word = name.upper() if len(name) <= 4 else name
            return ("an " if word[0].lower() in "aeiou" or word.isupper() and word[0] in "AEFHILMNORSX" else "a ") + word
        parts = " and ".join(readable(n) for n in state["unsupported"])
        have = ", ".join(PART_NAMES[t] for t in sorted(COMPONENT_TYPES))
        message = f"I can't simulate {parts} yet. Right now I can use {have}."
        return {"status": "gave_up", "spoken_summary": message, "problem": message,
                "explanation": {"summary": message, "lines": [], "try_next": [], "verified": [],
                                "fixes_made": []},
                "steps": add_step(state, "give_up", f"unsupported part: {parts}")}
    if state.get("fatal"):
        reason = f"a tool failed: {(state.get('problem') or '')[:200]}"
        spoken = "Sorry, something went wrong on my side. Please try again in a moment."
    else:
        reason = f"it still failed after {state.get('attempts', 0)} fix attempts"
        failed = [r for r in (state.get("report") or {}).get("results", []) if not r["ok"]]
        if state.get("problem_stage") == "verify" and failed:
            spoken = f"Sorry, I could not get this working after {state.get('attempts', 0)} tries. The last test showed: {failed[0]['detail']}."
        else:
            spoken = f"Sorry, I could not get this working after {state.get('attempts', 0)} tries. The details are on screen."
    return {"status": "gave_up",
            "spoken_summary": spoken,
            "explanation": {"summary": f"I could not get this working: {reason}.", "lines": [],
                            "try_next": [], "verified": (state.get("report") or {}).get("results", []),
                            "fixes_made": [{"attempt": h["attempt"], "stage": h["stage"], "change": h["change_made"]}
                                           for h in state.get("history", [])]},
            "steps": add_step(state, "give_up", reason)}


def build_card(state, status, steps):
    """What the chat shows for one build. Stored in the conversation so a reloaded page can redraw it."""
    passed = status == "passed"
    return {
        "status": status,
        "explanation": state.get("explanation", {}),
        "circuit_changes": circuit_changes(state.get("user_circuit"), state["circuit"]),
        "behavior_summary": state.get("behavior_summary", ""),
        "spoken_summary": state.get("spoken_summary", ""),
        "attempts": state.get("attempts", 0),
        "problem": None if passed else state.get("problem"),
        "steps": steps,
        "elapsed_ms": elapsed_ms(state),
    }


def make_respond(runs_conn):
    def respond(state):
        if state.get("intent") == "chat":
            steps = add_step(state, "done", "answered")
            msg = {"role": "assistant", "kind": "chat", "text": state.get("reply", ""),
                   "lines": state.get("reply_lines", ""), "spoken": state.get("spoken_summary", "")}
            return {"steps": steps, "messages": append_messages(state, msg)}
        status = state.get("status") or "gave_up"
        db.save_run(runs_conn, state["session_id"], state["user_text"], state["circuit"],
                    state.get("full_code", ""), state.get("hex", "") if status == "passed" else "",
                    status, state.get("attempts", 0))
        steps = add_step(state, "done", status)
        card = build_card(state, status, steps)
        msg = {"role": "assistant", "kind": "build", "status": status,
               "text": (card["explanation"] or {}).get("summary") or card["behavior_summary"] or card["problem"] or "",
               "card": card}
        return {"status": status, "steps": steps, "messages": append_messages(state, msg)}
    return respond


# ---------- routing ----------

def route_intent(state):
    return "chat" if state.get("intent") == "chat" else "build"


def route(state):
    """After any step: continue, try a fix, or give up."""
    if not state.get("problem"):
        return "next"
    if state.get("fatal"):
        return "give_up"
    if state.get("attempts", 0) >= MAX_FIXES or time.time() - state["started_at"] > TIME_CAP_S:
        return "give_up"
    return "fix"


def build_graph():
    runs_conn = db.connect()
    db.init_db(runs_conn)
    checkpointer = SqliteSaver(sqlite3.connect(db.DB_PATH, check_same_thread=False))

    g = StateGraph(State)
    for name, fn in [("understand", understand), ("plan_and_code", plan_and_code), ("preflight", preflight),
                     ("compile", compile_node), ("verify", verify), ("fix", fix), ("explain", explain),
                     ("give_up", give_up), ("respond", make_respond(runs_conn))]:
        g.add_node(name, fn)

    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", route_intent, {"build": "plan_and_code", "chat": "respond"})
    g.add_conditional_edges("plan_and_code", route, {"next": "preflight", "fix": "fix", "give_up": "give_up"})
    g.add_conditional_edges("preflight", route, {"next": "compile", "fix": "fix", "give_up": "give_up"})
    g.add_conditional_edges("compile", route, {"next": "verify", "fix": "fix", "give_up": "give_up"})
    g.add_conditional_edges("verify", route, {"next": "explain", "fix": "fix", "give_up": "give_up"})
    # Every fix goes through preflight again (instant), so a fix cannot sneak in a literal pin number.
    g.add_conditional_edges("fix", route, {"next": "preflight", "fix": "fix", "give_up": "give_up"})
    g.add_edge("explain", "respond")
    g.add_edge("give_up", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)


def stream_agent(graph, session_id, text, circuit, debug_break="", board=DEFAULT_BOARD, mode="auto", via="text"):
    """Run one message, yielding progress events and finally the result.
    Fields listed in `start` are per message. The last code, test results and the conversation come
    from the session checkpoint; a build resets its own fields (BUILD_RESET in plan_and_code)."""
    start = {
        "session_id": session_id, "board": board, "user_text": text, "circuit": circuit,
        "user_circuit": json.loads(json.dumps(circuit)),
        "problem": None, "problem_stage": None, "fatal": False, "unsupported": [], "status": None,
        "steps": [], "started_at": time.time(),
        "debug_break": debug_break, "break_done": False,
        "mode": mode, "via": via, "intent": "", "reply": "", "reply_lines": "", "say": "",
    }
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 60}
    for update in graph.stream(start, config=config, stream_mode="updates"):
        for node, values in update.items():
            if values and values.get("steps"):
                yield {"type": "step", **values["steps"][-1]}
            if values and values.get("say"):  # spoken right away in Live mode
                yield {"type": "say", "node": node, "text": values["say"]}

    final = graph.get_state(config).values
    if final.get("intent") == "chat":
        yield {"type": "result", "kind": "chat", "reply": final.get("reply", ""),
               "lines": final.get("reply_lines", ""), "spoken_summary": final.get("spoken_summary", ""),
               "steps": final.get("steps", []), "elapsed_ms": elapsed_ms(final)}
        return
    card = final["messages"][-1]["card"]
    passed = card["status"] == "passed"
    yield {
        "type": "result",
        "kind": "build",
        **card,
        "circuit": final["circuit"],
        "code": final.get("code", ""),
        "full_code": final.get("full_code", ""),
        "hex": final.get("hex", "") if passed else "",
        "checks": final.get("checks", []),
        "stimulus": final.get("stimulus", []),
        "report": final.get("report", {}),
        "history": final.get("history", []),
        "debug_break": debug_break,
    }


def session_snapshot(graph, session_id):
    """Everything a reloaded page needs to continue a session: the chat, circuit, board and code."""
    values = graph.get_state({"configurable": {"thread_id": session_id}}).values or {}
    builds = [m for m in values.get("messages") or [] if m.get("kind") == "build"]
    passed = bool(builds) and builds[-1].get("status") == "passed"
    return {
        "messages": values.get("messages") or [],
        "board": values.get("board") or DEFAULT_BOARD,
        "circuit": values.get("circuit"),
        "full_code": values.get("full_code") or "",
        "hex": values.get("hex", "") if passed else "",
    }
