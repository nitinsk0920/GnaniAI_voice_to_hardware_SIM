# MEMORY.md: handoff notes for the next session

Last updated: 2026-11-08 (end of Phase 4). The project moves to another laptop from here.
**Read order for a new session:** `CLAUDE.md` (rules, plan, status table, lessons) → this file (where we are, what's next) → `WORKFLOW.md` (agent design, only when changing the agent).

## About the user
- Strong in Python/FastAPI, a beginner with the simulator and frontend parts: explain those in plain language.
- Writes short, informal messages. Prefers clear yes/no answers, then the details.
- Runs the servers in their own terminals (`uvicorn --reload` and `npm run dev`). If you start servers for testing, stop them afterwards and say so.
- Demo languages: **English, Hindi, Kannada, Hinglish, Kanglish**. The user can judge Hindi and Kannada audio quality; Claude cannot hear audio.
- Wants each phase finished with exact run commands and what they should see.

## Where we are (all built and tested)
- **Phases 1 to 4 are done** (details in the CLAUDE.md status table).
- Flow: voice (Prisma) or text → LangGraph agent (plan → preflight → compile → headless verify → fix loop → explain) → browser runs the hex live → Timbre speaks the summary.
- Parts: LED, push button, servo, buzzer, potentiometer. Each is simulated, interactive on screen, and measured by `backend/evaluator.py`.
- UI: drawn wiring, pin dropdowns ("~" = PWM), live signal graph, streamed progress steps, explanation panel (verified ✓/✗, fixes made, walkthrough, try-next chips), wiring-change notice.
- Dev test dropdown: "break the first compile" and "timing bug" (4x too slow, so the measurement fails and the agent fixes it). **Use "timing bug" for the demo's self-correction.**
- LLM: Groq `openai/gpt-oss-120b` (about 1 s per call). Gemini `gemini-3.5-flash` is the backup (`LLM_PROVIDER=gemini` in `.env`).
- Gnani live-tested on Nov 8: Prisma heard an English clip exactly. Timbre returned valid MP3s in English, Hindi and Kannada. A full voice request was verified in headless Edge with a fake microphone. Timbre takes about 5 s for 2 sentences.

## Direction (user decision, Nov 9)
The user wants a **production-level** project, not a demo-only one: do not drop features just because of the deadline. Boards are added through **board profiles** (`backend/boards/<id>.json`, shared with the browser): compiler fqbn, chip, simulator engine, wokwi picture element, GND/5V pin names, Servo/tone PWM conflicts, notes and the pin map. The board travels with every request (`board` in `/run` and `/compile`), the LLM gets `board_facts()` in each plan/fix message, and the UI has a board dropdown that moves parts off pins the new board lacks (and says so).

| Board | Status |
|---|---|
| Arduino Uno | done |
| Arduino Nano | done (Nov 9): `arduino:avr:nano`; A6/A7 analog-only (`analog_only`, `adc` channel); picture scaled 1.8x in CSS |
| Arduino Mega 2560 | done (Nov 9): `arduino:avr:mega`, `mcu: atmega2560`. Profile generated from the core's `variants/mega/pins_arduino.h` (70 pins, PWM 2-13 and 44-46). `backend/boards/avr_chips.mjs` builds timers 0-5 (vectors from avr-libc `iomxx0_1.h`, x2 for avr8js), ports A-L with the 2560's INT0-7 / PCINT0 / PCINT2, a 16-channel ADC (A8-A15 via MUX5), 256 KB flash (avr8js then uses 3-byte return addresses) and data space up to 0x21FF. Servo uses Timer5 first (blocks PWM 44-46); tone() uses Timer2 (blocks 9, 10). `tests/test_mega.py` measures all of it (25/25). Wiring routes the right-hand header (22-53) out to the right |
| Raspberry Pi Pico | next, when the user asks. rp2040js (MIT) on npm; its bootrom is not in the package; compiling needs the arduino-pico core (large download; keep it inside the project if possible). Several unknowns, so test before promising |
| ESP32, STM32 | no open-source engine. Wokwi CI (cloud, quota 50 free minutes / 30 days) is parked by the user |

## Open items, in priority order (Phase 5: Nov 9, then record and post on Nov 10)
1. **Real microphone test.** The old laptop's mic is broken. Options: the new laptop's mic, earphones with a mic, or the phone (see 5).
2. **The user listens to Timbre Hindi and Kannada** (regenerate samples with `/speak`; the old ones were in the old laptop's `out/voice-test/`). If they sound wrong, try another voice: `GNANI_TTS_VOICE=Lakshmi` (v3.0 voices: Shivani [default], Avinash, Lakshmi, Kalpana, Shubham, Prakash, Devansh, Yashraj, Chetana, Vasudha, Riyaa, Tarika, Harshad).
3. **Demo script.** Two or three reliable commands, practised by typing first ("Speak replies" off saves credits):
   - "make the LED breathe" with LED1 on pin 13: the agent moves it to pin 9 and says so.
   - Add a button, then "when I press the button keep the LED fully on". Click the button on screen.
   - The same request again with Dev test = "timing bug", to show it fail the measurement and fix itself.
   - "slower" as a voice follow-up (the session remembers the code).
   - One request in Kannada or Kanglish.
4. **Polish ideas** (only if time allows):
   - Timbre streaming (SSE) so the reply starts sooner.
   - Hide the Dev dropdown behind a flag for the video.
   - Make the page look good in a screen recording.
5. **Phone as microphone** (planned, not built). Browsers allow the mic only on HTTPS or localhost. The plan:
   - add `@vitejs/plugin-basic-ssl` in `sim/`;
   - run Vite on `0.0.0.0`;
   - proxy `/api` → `http://localhost:8000` in `vite.config.js`;
   - change `API` in `sim/main.js` to `/api`;
   - the user accepts the certificate warning on the phone and allows Node through the Windows firewall (private networks).
6. **Not built:** 7-segment display (optional), LCD and DC motor (too big for the deadline). Phone mode as in 5.
7. **After the demo:** BYOK and deployment (CLAUDE.md section 9).

## Known behaviours and gotchas (more in CLAUDE.md "notes")
- LLM output varies between runs. Preflight guards against:
  - impossible thresholds;
  - check windows that contain a button press;
  - swapped from/to times;
  - blink/breathe windows too short to hold about two cycles (seen on the Mega run, Nov 9);
  - an LED that must fade on a non-PWM pin (the backend moves it, `move_to_pwm`).
- When the checks themselves are wrong, `fix` uses the checks-only prompt.
- A fix can still bend the code to fit a slightly wrong check (seen once: servo mapped 30-150 instead of 0-180).
- avr8js does not model input pull-ups, so released buttons are driven HIGH in `verify.mjs` and in `sim/simulator.js`.
- The Prisma REST API does not accept WebM, so `sim/voice.js` records PCM and builds a 16 kHz WAV.
- Credits: each voice request = 1 Prisma call + 1 Timbre call. Never loop on them in tests.
- PowerShell 5.1: `Get-Content -Raw` adds properties that break `ConvertTo-Json` bodies; use `[IO.File]::ReadAllText`.

## How to test (the scripts are in `tests/`)
- `.venv\Scripts\python tests\test_offline.py`: preflight rules, error line numbers. No LLM, no servers.
- `.venv\Scripts\python tests\test_verify.py`: 12 sketches (10 Uno, half deliberately wrong, plus 2 Nano) through compile, headless simulation and the evaluator. No LLM, no servers. About 25 s.
- `.venv\Scripts\python tests\test_mega.py`: Mega 2560 measured checks: 15 PWM pins, delay, servo, tone, port A button, A0/A8, attachInterrupt (25 checks). No LLM, no servers. About 30 s.
- Browser scenarios for `tests\drive.mjs`: `ui_timing_bug.json`, `ui_breathe_button.json`, `ui_wiring_rewire.json`, `ui_nano.json`, `ui_mega.json`.
- `.venv\Scripts\python tests\test_stream.py <session> <0|1> "<circuit json>" <request>`: one real agent run over SSE (needs the backend; uses the LLM).
- `node tests\drive.mjs tests\ui_timing_bug.json out\ui`: drives headless Edge through the real page and saves screenshots to `out\ui` (needs both servers; uses the LLM). See the header of `drive.mjs` for the fake-microphone flags.

## File map
- `backend/main.py`: routes `/health`, `/compile`, `/run` (SSE), `/transcribe`, `/speak`.
- `backend/boards/*.json`: board profiles. `backend/graph.py`: `BOARDS`, `board_facts()`, LangGraph nodes, prompts (`CODE_RULES`, `CHECKS_DOC`, `PLAN_SYSTEM`, `FIX_SYSTEM`, `FIX_CHECKS_SYSTEM`, `EXPLAIN_SYSTEM`), preflight, sim config.
- `backend/evaluator.py`: the measured checks. `backend/verify/verify.mjs`: the headless avr8js run.
- `backend/llm.py`: Groq or Gemini. `backend/gnani.py`: Prisma and Timbre. `backend/compiler.py`: arduino-cli. `backend/db.py`: SQLite.
- `sim/main.js`: page logic. `sim/circuit.js`: parts, wiring, interaction. `sim/signals.js`: pin history and graph. `sim/simulator.js`: browser avr8js. `sim/voice.js`: mic to WAV.
- `arduino/libraries/Servo`: Servo 1.3.0 (LGPL-2.1), used via `--libraries`.
