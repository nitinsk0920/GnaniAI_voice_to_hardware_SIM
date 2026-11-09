# MEMORY.md: handoff notes for the next session

Last updated: 2026-11-08 (chat interface added, on the new laptop at `C:\GnaniAI_voice_to_hardware_SIM`).
**Read order for a new session:** `PROJECT_GUIDE.md` (rules, plan, status table, lessons) → this file (where we are, what's next) → `WORKFLOW.md` (agent design, only when changing the agent).

## About the user
- Strong in Python/FastAPI, a beginner with the simulator and frontend parts: explain those in plain language.
- Writes short, informal messages. Prefers clear yes/no answers, then the details.
- Runs the servers in their own terminals (`uvicorn --reload` and `npm run dev`). If you start servers for testing, stop them afterwards and say so.
- Demo languages: **English, Hindi, Kannada, Hinglish, Kanglish**. The user can judge Hindi and Kannada audio quality; Claude cannot hear audio.
- Wants each phase finished with exact run commands and what they should see.

## Where we are (all built and tested)
- **Phases 1 to 4 are done** (details in the PROJECT_GUIDE.md status table).
- Flow: voice (Prisma) or text → LangGraph agent (plan → preflight → compile → headless verify → fix loop → explain) → browser runs the hex live → Timbre speaks the summary.
- Parts: LED, push button, servo, buzzer, potentiometer. Each is simulated, interactive on screen, and measured by `backend/evaluator.py`.
- UI: drawn wiring, pin dropdowns ("~" = PWM), live signal graph, wiring-change notice, code editor with line numbers.
- **Chat (Nov 8, user request):** a chat panel on the right. The `understand` node decides per message: a change request runs the build loop; a question ("why millis?", "explain line 7", "LED kitni der on rehta hai?") is answered from the code, circuit, measurements and conversation, in the user's language and script, with clickable line links. A build reply is a card: verified ✓/✗, fixes, wiring changes, **the code explained line by line** (click a row to select it in the editor), try-next chips (sent as `mode: build`). The conversation lives in the LangGraph checkpoint (`messages`), and a page reload restores chat, circuit, board and code (`GET /session/{id}`). Chat replies are spoken too when "Speak replies" is on (1 Timbre call each).
- Verified on Nov 8: mic and Timbre Hindi/Kannada (user), chat in English and Hinglish, timing-bug self-fix and "slower" in the chat.
- **Live mode (Nov 9, user request: "like ChatGPT/Gemini Live"):** the "● Go Live" button in the chat header starts a hands-free conversation. Pieces: `sim/live.js` (mic → 1,024-byte 16 kHz frames → `WS /live/stt` → Prisma streaming; streamed Timbre playback queue; ducking and interrupting; echo guard; reconnect), `main.py` (`/live/stt` relay, `/speak/stream`), `gnani.py` (`stt_stream_headers`, `speak_stream`), `graph.py` (`say` events: the `understand` ack and the `fix` line after a measured/compile failure). The user chose: streaming APIs from the start, and narration = start + self-fixes + finish.
  - Timing in a test run: the agent starts speaking about 3 s after you stop; a build with one self-fix took about 23 s, with spoken lines at the start, at the fix and at the end.
  - **Use earphones and switch "🎧 Earphones" on for the demo**: with speakers, echo cancellation must stay on and it damps your voice while the agent talks, so interrupting is weak (it did not work at all with the fake test mic). The user still needs to try Live mode with the real mic, on speakers and on earphones.
  - Credits per spoken turn: Prisma streams only speech segments; each spoken line is one Timbre call (a build is usually 2-3).
- **Groq free tier limit:** gpt-oss-120b allows about 8,000 tokens a minute, roughly one build a minute (understand + plan + explain, more with fixes). `llm.py` now retries briefly and otherwise switches to Gemini automatically (prints `[llm] ... using the backup provider gemini` in the uvicorn window). For the recording, leave about a minute between builds or the backup will answer some of them. A paid Groq tier would remove this.
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

## Next big step: a real component library with full pins and wiring (agreed with the user, Nov 9)
The user wants **as many components as possible, each with its real pins** (e.g. a relay module has VCC, GND, IN on the control side and COM, NO, NC on the switch side). Today each part has exactly one Arduino pin and the 5V/GND wires are drawing only. Plan, in order (do it carefully, with tests, as was done for the Mega):

1. **Foundation (about 2 days):**
   - **Netlist circuit format:** parts with named pins, plus wires between any two pins, e.g. `{"parts":[{"id":"RELAY1","type":"relay_module"}], "wires":[["RELAY1.IN","BOARD.7"],["RELAY1.COM","BOARD.5V"],["RELAY1.NO","LED2.A"]]}`. The backend writes `#define` lines for the Arduino-connected signal pins (`RELAY1_IN_PIN`).
   - **Logic-level circuit solver**, shared by the browser and the verifier (like `avr_chips.mjs`):
     - group connected pins into nets;
     - drive nets from Arduino outputs, 5V and GND, and part outputs;
     - merge nets through closed switches, buttons and relay contacts;
     - compute each load from its pins (an LED lights when anode is HIGH and cathode LOW; PWM gives brightness).
     - Not SPICE; this is the Wokwi/Tinkercad level.
   - **One model file per component:** pins (name, role), picture element, behaviour (step function), what the verifier can measure, and wiring hints.
   - **UI:** real pin-to-pin wiring (click a pin, then another pin), wires drawn from the netlist, and auto-wiring when a part is added.
   - **Agent:**
     - the plan prompt gets a compact component catalog;
     - checks can target parts ("LED2 lit", "relay closed", "motor speed");
     - preflight finds wiring mistakes: an LED without a resistor, an output shorted to GND or 5V, two outputs on one net, a relay coil driven straight from a pin, a floating input.
   - Convert the 5 existing parts and keep every test passing.
2. **Simple parts:** relay module (VCC, GND, IN | COM, NO, NC), bare relay KS2E (`wokwi-ks2e-m-dc5`), slide switch (1, 2, 3), tilt switch, DIP switch (8), resistor, NPN transistor (B, C, E; own picture), diode (own picture), RGB LED (R, G, B, COM), 7-segment (A-G, DP, COM), LED bar graph, photoresistor module (AO, DO), NTC temperature (OUT), PIR (OUT), flame, gas and sound sensors (AO, DO), slide potentiometer, analog joystick (VRX, VRY, SW).
3. **Timing parts:** stepper motor with A4988 (STEP, DIR, EN) or ULN2003 (IN1-4), DC motor with L298N (IN1, IN2, ENA, OUT1, OUT2; no wokwi picture, draw our own), HC-SR04 (TRIG, ECHO; simulate the echo pulse), rotary encoder KY-040 (CLK, DT, SW), membrane keypad 4x4 (R1-R4, C1-C4).
4. **Protocol parts (each needs its own chip emulated in JS):** LCD 16x2/20x4 first (HD44780 4-bit, then I2C), SSD1306 OLED, NeoPixel, TM1637, DHT22, MPU6050, DS1307, HX711, IR remote.

wokwi-elements has pictures for: 7segment, analog-joystick, biaxial-stepper, big-sound-sensor, buzzer, dht22, dip-switch-8, ds1307, flame-sensor, gas-sensor, hc-sr04, heart-beat-sensor, hx711, ili9341, ir-receiver, ir-remote, ks2e-m-dc5 (relay), ky-040, lcd1602, lcd2004, led, led-bar-graph, membrane-keypad, microsd-card, mpu6050, neopixel, neopixel-matrix, ntc-temperature-sensor, photoresistor-sensor, pir-motion-sensor, potentiometer, pushbutton, resistor, rgb-led, servo, slide-potentiometer, slide-switch, sound sensors, ssd1306, stepper-motor, tilt-switch, tm1637.

**Until then:** a request for any other part stops at once with an honest message ("I can't simulate a relay yet. Right now I can use ..."). It comes from the `unsupported` field in the plan reply, or from preflight seeing a hardware library include such as `LiquidCrystal.h` (`HARDWARE_LIBRARIES` in `graph.py`). This was added after a run where the model quietly dropped the LCD and "passed" with an LED check; that must never happen.

## Open items, in priority order (Phase 5: Nov 9, then record and post on Nov 10)
1. ~~Real microphone test~~ and 2. ~~Timbre Hindi/Kannada~~: both confirmed working by the user on Nov 8 (new laptop). Other voices if ever needed: `GNANI_TTS_VOICE=Lakshmi` (v3.0 voices: Shivani [default], Avinash, Lakshmi, Kalpana, Shubham, Prakash, Devansh, Yashraj, Chetana, Vasudha, Riyaa, Tarika, Harshad).
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
7. **BYOK: done (Nov 9)**, see PROJECT_GUIDE.md section 9. Next: deployment. Decided with the user on Nov 9:
   - **Host: Hugging Face Spaces (Docker, free CPU tier: 2 vCPU, 16 GB RAM, HTTPS, WebSockets)**, not Render free (0.1 CPU / 512 MB would make compile + simulate slow enough to hit the 90 s cap; sleeps after 15 min).
   - **BYOK for both keys:** visitors always bring their own **Gnani key** (Prisma/Timbre) **and their own LLM key** (Groq or Gemini); the deployed server uses no keys of ours. Keys live only in the browser tab, go with each request (headers; the Live WebSocket gets the Gnani key in its first message, never in the URL) and are never stored or logged by the backend. Add a per-IP rate limit. SQLite sessions are lost on restart (fine).
   - **Order (user, Nov 9):** BYOK (done), then Dockerfile + deploy, then the README with a one-page solution pitch (the submission form asks for a project link with a pitch of at most 1 page). The repo must be public, or the link must point to something public, for the judges to open it.
   - BYOK details: `backend/keys.py` (context variable per request; `keys.iterate` runs each step of the streamed agent inside it, LangGraph's worker threads copy it), `sim/keys.js`, the 🔑 API keys dialog in `index.html`. A rejected key (401/403) moves to the backup LLM provider if the visitor gave both keys. Live mode stops instead of reconnecting when the connection is refused (bad or missing Gnani key) and closes its panel with the reason.

## Known behaviours and gotchas (more in PROJECT_GUIDE.md "notes")
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
- `.venv\Scripts\python tests\test_offline.py`: preflight rules, error line numbers, chat helpers, LLM retry/backup rules (fake HTTP), and a whole build → chat → build graph run with a scripted fake LLM (real compile and simulation). No LLM, no servers.
- `.venv\Scripts\python tests\test_verify.py`: 12 sketches (10 Uno, half deliberately wrong, plus 2 Nano) through compile, headless simulation and the evaluator. No LLM, no servers. About 25 s.
- `.venv\Scripts\python tests\test_mega.py`: Mega 2560 measured checks: 15 PWM pins, delay, servo, tone, port A button, A0/A8, attachInterrupt (25 checks). No LLM, no servers. About 30 s.
- `node tests\test_live.mjs`: the Live echo guard (English, Hindi, Kannada). No servers.
- BYOK in the browser with fake keys (no credits; Groq and Gnani just refuse them): a backend with `REQUIRE_USER_KEYS=1` on another port and Vite pointed at it, e.g. `cd backend; $env:REQUIRE_USER_KEYS=1; ..\.venv\Scripts\python -m uvicorn main:app --port 8010` and `cd sim; $env:VITE_API_URL="http://localhost:8010"; npx vite --port 5180`, then `$env:APP_URL="http://localhost:5180/"; $env:EXTRA_ARGS="--use-fake-ui-for-media-stream|--use-fake-device-for-media-stream"; node tests\drive.mjs tests\ui_byok.json out\ui-byok`.
- `.venv\Scripts\python tests\check_byok.py <server url>`: the keys from `.env` sent as a visitor's (real: one build, one Timbre line, one Prisma connect). Use it on the deployed site too.
- Live scenarios with a fake microphone (Gnani credits: 1 Prisma stream + 2-4 Timbre lines each). First `.venv\Scripts\python tests\make_fake_mic.py` (builds `out\live-probe\fake_mic*.wav`; 2 short Timbre calls the first time, then cached), then:
  `$env:EXTRA_ARGS = "--use-fake-ui-for-media-stream|--use-fake-device-for-media-stream|--use-file-for-fake-audio-capture=<full path>\fake_mic.wav%noloop"; node tests\drive.mjs tests\ui_live.json out\ui-live`
  (`ui_live_interrupt.json` uses `fake_mic_interrupt.wav`: a second question at 9.5 s, while the agent is talking).
- Browser scenarios for `tests\drive.mjs`: `ui_chat.json` (build, question, Hinglish question, line click, chip, reload; about 7 LLM calls), `ui_timing_bug.json`, `ui_breathe_button.json`, `ui_wiring_rewire.json`, `ui_nano.json`, `ui_mega.json`. The scenarios read the latest build card in `#messages`. Only `ui_chat` and `ui_timing_bug` switch "Speak replies" off; turn it off in the others before running them, or they use Timbre credits.
- `.venv\Scripts\python tests\test_stream.py <session> <0|1> "<circuit json>" <request>`: one real agent run over SSE (needs the backend; uses the LLM).
- `node tests\drive.mjs tests\ui_timing_bug.json out\ui`: drives headless Edge through the real page and saves screenshots to `out\ui` (needs both servers; uses the LLM). See the header of `drive.mjs` for the fake-microphone flags.

## File map
- `backend/main.py`: routes `/health`, `/compile`, `/run` (SSE, one chat message), `/session/{id}`, `/transcribe`, `/speak`, `/speak/stream` (Live), `WS /live/stt` (Live).
- `backend/keys.py` and `sim/keys.js`: the visitor's own API keys (BYOK).
- `sim/live.js`: Live mode (see above).
- `backend/boards/*.json`: board profiles. `backend/graph.py`: `BOARDS`, `board_facts()`, LangGraph nodes (`understand` first), prompts (`CODE_RULES`, `CHECKS_DOC`, `PLAN_SYSTEM`, `FIX_SYSTEM`, `FIX_CHECKS_SYSTEM`, `EXPLAIN_SYSTEM`, `CONVERSE_SYSTEM`, shared `SPOKEN_RULES`/`WRITTEN_LANGUAGE`), preflight, sim config, `session_snapshot`.
- `sim/chat.js`: the chat panel (bubbles, live steps, build card, line-by-line table, safe text formatting: model text is never inserted as HTML).
- `backend/evaluator.py`: the measured checks. `backend/verify/verify.mjs`: the headless avr8js run.
- `backend/llm.py`: Groq or Gemini. `backend/gnani.py`: Prisma and Timbre. `backend/compiler.py`: arduino-cli. `backend/db.py`: SQLite.
- `sim/main.js`: page logic. `sim/circuit.js`: parts, wiring, interaction. `sim/signals.js`: pin history and graph. `sim/simulator.js`: browser avr8js. `sim/voice.js`: mic to WAV.
- `arduino/libraries/Servo`: Servo 1.3.0 (LGPL-2.1), used via `--libraries`.
