# Voice-to-Hardware Simulator

**Talk to an AI agent, and it builds working Arduino projects for you: it writes the code, runs it in a
simulator, measures the pins to prove it works, fixes itself, and explains every line.**

- **Try it online:** https://voice-to-hardware.onrender.com (bring your own API keys, see below)
- **Demo video (LinkedIn):** https://lnkd.in/p/gusZK59H
- **One-page solution pitch:** [PITCH.md](PITCH.md)
- Entry for the **Great Indian AI Internship Challenge** by Gnani AI. Voice by Gnani **Prisma** and **Timbre**.

---

## Contents
1. [The idea](#1-the-idea)
2. [Features](#2-features)
3. [How it works](#3-how-it-works)
4. [How Gnani AI is used](#4-how-gnani-ai-is-used)
5. [Tech stack](#5-tech-stack)
6. [Project structure](#6-project-structure)
7. [Try it online](#7-try-it-online)
8. [Run it on your computer](#8-run-it-on-your-computer)
9. [Tests](#9-tests)
10. [Deployment](#10-deployment)
11. [Current limits](#11-current-limits)
12. [Future work](#12-future-work)
13. [Credits and licenses](#13-credits-and-licenses)

---

## 1. The idea

AI can write Arduino code, but it cannot tell whether that code really works. You find out only
after wiring real hardware. This project closes that gap with a **loop**:

> voice → code → compile → simulate → **measure** → fix → explain → speak

You design a small circuit on a virtual Arduino, then say (or type) what it should do, in English,
Hindi, Kannada or a mix such as Hinglish. The agent writes the code for **your** pins, the real
Arduino compiler builds it, a chip-level simulator runs it, and the measured pin signals decide
whether it passed. If a check fails, the agent gets the measured numbers, fixes its code and tries
again. When it passes, the circuit runs live in your browser and the agent explains the code.

## 2. Features

- **Circuit editor:** Arduino **Uno, Nano or Mega 2560**, with LEDs, push buttons, servos, buzzers and
  potentiometers. Pick a pin per part; wires are drawn for you. Click buttons and turn knobs while the code runs.
- **Chat with the agent:** ask it to build something, or ask questions about the code or circuit. It
  answers in the language and script you use.
- **Measured checks:** blink rate, steady on/off, LED brightness and fading (PWM), servo angle and
  sweep, buzzer pitch, and reactions to button presses and knob turns.
- **Self-correction:** compile errors and failed measurements go back to the agent (up to 3 fixes).
  A dev switch can inject a bug on purpose to show this.
- **Explanations:** a summary, a line-by-line table (click a row to highlight the line), what was
  verified, what was fixed, and suggestions for what to try next.
- **Voice:** push-to-talk (🎤), and **Live mode**: a hands-free conversation like Gemini Live. The
  agent says what it is doing and when it fixes itself, and you can interrupt it by talking.
- **Live simulation and signal graph:** the passing code runs in the browser in real time, with a
  graph of every pin.
- **Sessions:** follow-ups such as "slower" or "add a buzzer on pin 8" build on the current circuit
  and code; the session comes back after a page reload.
- **Bring your own key:** on the public site each visitor uses their own LLM and Gnani keys, kept in
  their browser only.
- **Honest:** if you ask for a part it cannot simulate yet (an LCD, a relay), it says so instead of faking a pass.

## 3. How it works

```
 You (voice or text)
   │                      Gnani Prisma: speech to text
   ▼
 Browser (sim/) ──── POST /run (progress streams back live) ────►  FastAPI backend (backend/)
                                                                    │
            LangGraph agent loop                                    ▼
   understand ─► plan_and_code ─► preflight ─► compile ─► verify ─► explain ─► respond
                       ▲                          │          │
                       └──────────── fix ◄────────┴──────────┘   (at most 3 fixes, then an honest "give up")
                                                                    │
 Browser: shows the code, runs it live (avr8js), draws the signal graph,  ◄────┘
          explains each line, speaks the summary (Gnani Timbre)
```

Step by step, for "make the LED on pin 6 breathe":

| Step | What happens |
|---|---|
| **understand** | The LLM decides: a question (answer it) or a build request (start the loop). |
| **plan_and_code** | The LLM writes the Arduino code and the **checks** to measure, e.g. "LED1 brightness rises and falls every 1 to 3 s". |
| **preflight** | Instant rule checks: is pin 6 a PWM pin, do pins clash, are the check numbers sensible? The backend writes the `#define LED1_PIN 6` lines itself, so **your circuit owns the pin numbers**. |
| **compile** | `arduino-cli` builds a `.hex` file, the same file a real board would get. |
| **verify** | Node runs the `.hex` on a simulated ATmega chip (**avr8js**) for a few simulated seconds, presses buttons and turns knobs on schedule, and records every pin change. `evaluator.py` compares the measurements with the checks. |
| **fix** | On a compile error or a failed check, the LLM gets the exact error or the measured numbers ("period 4.1 s, expected 1 to 3 s") and corrects the code. |
| **explain** | Summary, line-by-line notes, suggestions, and a short spoken sentence. |

The key rule: **the measurements decide success, not the LLM.**

## 4. How Gnani AI is used

| Gnani model | Where | Details |
|---|---|---|
| **Prisma** (speech-to-text) | 🎤 button | One recording (up to 30 s), sent as 16 kHz WAV (encoded in the browser), with English, Hindi and Kannada hints. `codemix` output keeps words like "LED" and "servo" in English letters. |
| **Prisma streaming** | Live mode | The microphone streams in 32 ms frames over a WebSocket (relayed by the backend). Prisma detects when you start and stop talking. |
| **Timbre** (text-to-speech) | Spoken replies | Short summaries (never code) in English, Hindi or Kannada; the language is picked from the script. |
| **Timbre streaming** | Live mode | Audio is played while it is generated (first sound after about 1.5 s). Talking over it interrupts it. |

All Gnani calls live in one file, [backend/gnani.py](backend/gnani.py).

## 5. Tech stack

| Part | Technology | Why |
|---|---|---|
| Voice | Gnani **Prisma** and **Timbre** (REST and streaming) | Indian languages and code-mixed speech |
| Backend | Python 3.12, **FastAPI**, uvicorn | Simple, fast, built-in streaming and WebSockets |
| Agent loop | **LangGraph** | An explicit loop with retry edges and a cap; saves each session's state |
| Memory | **SQLite** (LangGraph checkpointer) | Built into Python, no database server |
| LLM | **Groq** (`gpt-oss-120b`) or **Gemini** (Flash), each the other's backup | Fast; OpenAI-compatible, so one small function covers both |
| Compiler | **arduino-cli** with the `arduino:avr` core | The official Arduino compiler: real code for real boards |
| Simulator | **avr8js** (headless in Node, live in the browser) | Simulates the actual chip instruction by instruction (timers, PWM, interrupts), so measurements are trustworthy |
| Part pictures | **wokwi-elements** | Realistic board, LED, servo, button and buzzer components |
| Frontend | **Vite** + plain JavaScript, HTML, CSS | No framework needed; small and fast |
| Deployment | **Docker** on Render | One container serves the page and the API |

## 6. Project structure

```
backend/
  main.py          FastAPI app: /run (agent, streamed), /compile, /session, /transcribe, /speak, /speak/stream, WS /live/stt, /health
  graph.py         the agent: LangGraph nodes, prompts, preflight rules, board facts
  llm.py           the only place that calls the LLM (Groq or Gemini, retries and backup)
  gnani.py         the only place that calls Gnani (Prisma and Timbre)
  compiler.py      arduino-cli wrapper (temp folder, argument list, timeout)
  evaluator.py     turns pin recordings into pass/fail checks
  verify/verify.mjs  headless avr8js run used by the verifier
  boards/          board profiles (Uno, Nano, Mega) and the shared chip setup (avr_chips.mjs)
  keys.py          visitors' own API keys, per request (never stored or logged)
  limits.py        per-visitor limits for the public server
  db.py            SQLite helpers
sim/               the web page (Vite)
  main.js          page logic       circuit.js  parts and wiring     simulator.js  live avr8js
  chat.js          chat panel       live.js     Live mode            voice.js      mic to WAV
  signals.js       signal graph     keys.js     the API keys dialog
arduino/libraries/Servo/   Servo library (LGPL-2.1), used by the compiler
tests/             offline tests, live checks, headless-browser scenarios
Dockerfile, render.yaml    deployment
PROJECT_GUIDE.md, WORKFLOW.md, MEMORY.md   design notes and the build log
```

## 7. Try it online

1. Open https://voice-to-hardware.onrender.com. On the free plan the site sleeps when nobody uses it,
   so the first visit can take about a minute.
2. Click **🔑 API keys** and add:
   - a **Groq** key (free at https://console.groq.com/keys) and/or a **Gemini** key (https://aistudio.google.com/apikey);
   - a **Gnani** key (https://app.gnani.ai) for voice. Text chat works without it.

   Keys stay in your browser and go only with your own requests. The server never stores or logs them.
3. Add parts, type or say what they should do, and watch the agent build, measure, fix and explain.

Try: *"make the LED on pin 6 breathe slowly"*, then *"slower"*, or with a button and servo:
*"when I press the button, move the servo to 90 degrees"*.

## 8. Run it on your computer

You need **Git**, **Node.js 22.12+** (24 LTS tested), **Python 3.12** and **arduino-cli** (on PATH).

```powershell
git clone https://github.com/nitinsk0920/GnaniAI_voice_to_hardware_SIM.git
cd GnaniAI_voice_to_hardware_SIM
arduino-cli core install arduino:avr
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
cd sim; npm install; cd ..\backend\verify; npm install; cd ..\..
```

Optional: a `.env` file in the project folder with your own keys, so you don't have to enter them in the page:
```
LLM_PROVIDER=groq
GROQ_API_KEY=...
GEMINI_API_KEY=...
GNANI_API_KEY=...
```

Run in two terminals, then open http://localhost:5173:
```powershell
cd backend; ..\.venv\Scripts\python -m uvicorn main:app --reload --port 8000
cd sim; npm run dev
```
(macOS/Linux: use `.venv/bin/python` and `/` in paths.)

## 9. Tests

| Command | What it checks | Needs |
|---|---|---|
| `python tests/test_offline.py` | Preflight rules, the whole agent graph with a scripted fake LLM (real compile and simulation), LLM retry and backup, keys and limits | nothing |
| `python tests/test_verify.py` | 12 sketches (half deliberately wrong) through compile, simulation and the evaluator | nothing |
| `python tests/test_mega.py` | Mega 2560: all 15 PWM pins, servo, tone, analog inputs, interrupts (25 checks) | nothing |
| `node tests/test_live.mjs` | Live mode's echo filter (English, Hindi, Kannada) | nothing |
| `node tests/drive.mjs tests/ui_*.json out/ui` | The real page in headless Edge, including a fake microphone for voice | running servers |
| `python tests/check_byok.py <url>` | A running server with your keys sent the way a visitor's are | API keys (a few credits) |

## 10. Deployment

The [Dockerfile](Dockerfile) builds one image: the page (built with Vite) plus Python, Node and
arduino-cli with the AVR core. FastAPI serves the page and the API from one address. The public
server sets `REQUIRE_USER_KEYS=1` (never use server-side keys) and `RATE_LIMIT=1` (per-visitor limits,
one build at a time on the small free plan).

It runs on **Render's free plan** using [render.yaml](render.yaml) (Render dashboard: New > Blueprint),
and redeploys on every push to `main`. To try the image locally:
```
docker build -t v2h .
docker run --rm -p 7860:7860 v2h        # open http://localhost:7860
```

## 11. Current limits

- **Parts:** five types (LED, push button, servo, buzzer, potentiometer). Each part uses one Arduino pin;
  5V and GND wires are drawn for clarity, not simulated.
- **Boards:** Arduino Uno, Nano and Mega 2560 (AVR chips only).
- **Speed on the free server:** a build takes about 20 s (a few seconds on a laptop), and the site sleeps when idle.
- **Simulation:** logic levels and timing, not analog electronics (no voltages, currents or burnt LEDs).
- **Sessions** are lost when the server restarts.

## 12. Future work

### More boards
| Board | Plan |
|---|---|
| **Raspberry Pi Pico** (RP2040) | Use **rp2040js** (open source) as the simulator and the arduino-pico core to compile. |
| **ATtiny85** and other AVR chips | Possible with avr8js; needs a board profile and a chip setup like the Mega's. |
| **ESP32, STM32** | No open-source browser simulator today. Options: a cloud simulator (Wokwi CI) or a QEMU-based backend. |

Boards are added through **board profiles** (`backend/boards/<id>.json`: compiler name, chip, pin map,
PWM pins, picture), so the agent, the verifier and the page all learn a new board from one file.

### All kinds of components, with real pins and wiring
1. **Foundation:** a netlist circuit format where every part has its **real pins** and wires connect
   any two pins (for example a relay module: VCC, GND, IN, COM, NO, NC). A small logic-level circuit
   solver, shared by the page and the verifier, works out what each part sees. Click-to-wire in the
   UI, and preflight warnings for wiring mistakes (an LED without a resistor, an output shorted to GND,
   a floating input).
2. **Simple parts:** relay module, slide/tilt/DIP switches, resistor, transistor, diode, RGB LED,
   7-segment display, LED bar graph, light, temperature, motion, flame, gas and sound sensors, joystick.
3. **Timing parts:** stepper motor (A4988 or ULN2003 driver), DC motor with L298N, ultrasonic distance
   sensor HC-SR04, rotary encoder, 4x4 keypad.
4. **Protocol parts** (each needs its chip emulated): 16x2 and 20x4 LCDs, SSD1306 OLED, NeoPixel LEDs,
   TM1637 display, DHT22, MPU6050, DS1307 clock, HX711 scale, IR remote.

New check types follow the parts: "relay closed", "motor speed", "LCD shows text", "distance read".

### Taking it to production
- **Isolated build workers:** run compile and simulation in a job queue with separate, sandboxed
  workers (no network, CPU and memory limits), so many users can build at once and untrusted code is contained.
- **Scaling and speed:** a paid server that does not sleep, caching of compiled builds, the page on a CDN.
- **Accounts and storage:** sign-in, saved projects and share links, a real database (PostgreSQL)
  instead of SQLite, and per-user quotas.
- **Reliability:** health checks and alerts, structured logs, metrics (build time, pass rate, fixes
  needed), error tracking, and automatic tests on every push (CI) before deploying.
- **Security:** a content security policy, rate limits at the edge, dependency updates, and a
  security review of the key handling and the WebSocket relay.
- **AI quality:** a test set of real requests in several languages, scored automatically on every
  prompt or model change, plus cost and latency tracking. Use Gnani's own LLM (Evon) if it becomes
  available through an API.
- **Real hardware:** download the sketch, or upload it to a real board from the browser (Web Serial).
- **Learners and teachers:** guided lessons, classroom mode, a mobile-friendly layout, accessibility
  checks, and more Indian languages for both the voice and the interface.

## 13. Credits and licenses

- Voice by **Gnani AI**: Prisma (speech-to-text) and Timbre (text-to-speech).
- The simulation runs on **[avr8js](https://github.com/wokwi/avr8js)** and the parts are drawn with
  **[wokwi-elements](https://github.com/wokwi/wokwi-elements)**, both open source by Uri Shaked
  (MIT license). Full texts in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Servo** library 1.3.0 by Arduino (LGPL-2.1), in `arduino/libraries/Servo`.
- LLMs via **Groq** and **Google Gemini**.

#GnaniAI #GreatIndianAIInternshipChallenge
