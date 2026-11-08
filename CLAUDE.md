# CLAUDE.md: Voice-to-Hardware Simulator

Last updated: 2026-11-08. Read this file fully, then **`MEMORY.md` (handoff: current state, open items, how to test)**, then `WORKFLOW.md` (agent design) when changing the agent. On a new machine, start with section 5b.
Working folder: `D:\gnani`. Entry for the **Great Indian AI Internship Challenge (Gnani AI)**.
**Deadline: November 10, 2026 (no extensions).** Build locally until the demo video is posted. BYOK and deployment come after (section 9).

## 1. What we are building

The user designs a virtual Arduino circuit (an Arduino Uno with parts such as an LED, a button, a servo) and then tells an AI agent, by voice or text, how the hardware should behave: "sweep the servo slowly, and snap to 90 degrees when I press the button". The agent:

1. writes the Arduino code for **the user's circuit and pins**,
2. compiles it with `arduino-cli`,
3. runs it in a simulator and **measures the simulated pins** to check it really does what was asked,
4. fixes its own code if compiling or the check fails (capped retries),
5. shows the code, **explains it in plain language**, and speaks a short summary.

The user keeps refining by voice ("slower", "add a buzzer on pin 8"). The standout idea is the **closed loop**: voice, code, compile, simulate, measure, fix, explain, speak. It is not a chatbot role-play.

## 2. Contest requirements

- Must use Gnani AI models/APIs. The contest email says the only hard requirement is Gnani's **voice** models: **Prisma** (speech-to-text) and **Timbre** (text-to-speech, beta). **Evon** (30B reasoning model) is listed as available on Hugging Face.
- **Open question: is Evon callable through the Gnani API platform?** The email says the platform "has our voice models" and lists Evon as on Hugging Face. Do not assume. The user will confirm from the docs. All LLM calls go through one function in `llm.py` so the provider can be swapped (Evon if available, otherwise another hosted LLM API such as Groq). Prisma and Timbre are always Gnani.
- 5,000 programme credits and up to 3 free API keys (app.gnani.ai). **Do not burn credits in test loops, especially audio.** Support is on Gnani's Discord or email.
- Deliverable: a working project plus a public demo video on LinkedIn or X with `#GnaniAI #GreatIndianAIInternshipChallenge`. Selection leans on public engagement, so a clear visual demo matters as much as the code.
- **Exact Gnani API request formats are not known. Never guess endpoints, field names, auth headers or audio formats. Ask the user to paste the docs or screenshots first.**

## 3. Ground rules

### Keep it simple
- Simple stack, simple code, few files, plain functions over classes, short readable comments. The user is strong in Python/FastAPI and a beginner with the simulator and frontend parts, so explain those in plain language.
- **No** React, TypeScript, Docker, auth, Redis, or LangChain code. **Plain LangGraph** for the agent loop, and **tiny SQLite** for session state (details in `WORKFLOW.md`). If a LangGraph or dependency problem costs more than about 30 minutes, tell the user and fall back to a plain Python loop with the same node functions.
- Scope: **4 to 5 parts that work perfectly** (LED, button, servo, buzzer, maybe a potentiometer), not many that are flaky.

### How to work (self-debugging while building)
- **Run everything you write.** Never hand over untested code.
- On an error: read the full message, fix the root cause, run again. After 3 failed attempts on the same error, stop and tell the user what you tried and what you suspect.
- Install dependencies as needed, **only inside the project** (Python in the venv, npm in `sim/` and `backend/verify/`), only well-known packages, and **tell the user which packages you added**. Keep `requirements.txt` and `package.json` current.
- Never change system settings, PATH, or anything outside `D:\gnani`.
- One phase at a time. The previous phase must still work. At the end of a phase, give the exact commands to run it, say what the user should see, and update the status table (section 10).
- Ask the user for exact error text or a screenshot when something fails on their side.

## 4. Tech stack (final)

| Part | Choice |
|---|---|
| Backend | Python, FastAPI, uvicorn |
| Agent loop | LangGraph: generate, preflight, compile, verify, fix, explain. No LangChain code. |
| State | SQLite via stdlib `sqlite3` (`backend/gnani.db`) plus LangGraph's SQLite checkpointer. In-memory `MemorySaver` is the fallback. |
| Compile | `arduino-cli` via `subprocess` (argument lists, never shell strings) |
| Headless verify | Node script using `avr8js` (`backend/verify/verify.mjs`) |
| Frontend | Vite, **vanilla JavaScript**, plain HTML/CSS |
| Simulator (browser) | `avr8js` (npm) |
| Visual parts | `@wokwi/elements` (npm web components: `<wokwi-led>`, `<wokwi-pushbutton>`, `<wokwi-servo>`, ...) |
| HTTP to AI services | `httpx` |
| LLM | via `llm.py` (Evon if available through an API, else a swappable hosted LLM) |
| Speech | Prisma (STT) and Timbre (TTS) via `gnani.py` |

Licenses: avr8js (Copyright 2019-2025 Uri Shaked) and wokwi-elements (Copyright 2020 Uri Shaked) are both **MIT**. Keep `THIRD_PARTY_NOTICES.md` with both notices and the MIT text. Credit them in the demo post ("Built on avr8js and wokwi-elements by Uri Shaked").

## 5. Environment (already set up and verified)

- **OS:** Windows, PowerShell. Projects on `D:`. The user's Desktop sits under OneDrive, so keep the project in `D:\gnani`.
- **Node** v24.14.1, **Git** 2.53.0, **Python** 3.14.3 (very new).
- **Python venv:** LangGraph and its dependencies may lag behind 3.14. Run `py -0` to see installed versions and **prefer 3.12 or 3.13** (install from python.org if missing), e.g. `py -3.12 -m venv .venv`. If only 3.14 exists, try it and switch on the first build or compatibility error (for example `pydantic-core`). If PowerShell blocks `.venv\Scripts\Activate.ps1`, run `Set-ExecutionPolicy -Scope Process Bypass` in that window first.
- **arduino-cli** 1.5.2-rc.1 (a release candidate; if odd errors appear, try the stable release). Installed at `D:\tools\arduino-cli.exe`; `D:\tools` is on the user PATH and works from any folder. Python code should find it with `shutil.which("arduino-cli")`. Node is on PATH as `node`.
- **Board core:** `arduino:avr` 1.8.8 installed (Uno). An `esp32:esp32` core also exists from earlier work and is irrelevant.

### Verified compile test
`D:\gnani\blink\blink.ino` (keep it as a manual test):
```cpp
void setup() { pinMode(13, OUTPUT); }
void loop() { digitalWrite(13, HIGH); delay(500); digitalWrite(13, LOW); delay(500); }
```
Works from `D:\gnani`:
```powershell
arduino-cli compile --fqbn arduino:avr:uno --output-dir out blink
```
It produces `out\blink.ino.hex` (about 2.6 KB). **Use `blink.ino.hex`, never the `with_bootloader` files.** Measured compile time: **about 1.8 s** (cached).

### Lessons already learned
- The last argument of `arduino-cli compile` is the sketch folder relative to the current directory, and the **folder name must equal the `.ino` file name** (`blink/blink.ino`). Wrong directory gives "Can't open sketch" or "main file missing from sketch".
- After changing PATH, VS Code and PowerShell must be fully restarted.
- Avoid Notepad for `.ino` files (it can save `.ino.txt`).

## 5b. Setup on a new machine

Install (then restart VS Code and PowerShell so PATH changes apply):
1. Git (`git --version`)
2. Node.js LTS 20 or newer (`node -v`)
3. Python 3.12 from python.org, with "Add to PATH" ticked (`py -3.12 --version`)
4. arduino-cli, placed in a folder on PATH (`arduino-cli version`), then once: `arduino-cli core install arduino:avr`

Then:
```powershell
git clone <private repo url> D:\gnani
cd D:\gnani
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
cd sim; npm install; cd ..\backend\verify; npm install; cd ..\..
```
Create `D:\gnani\.env` by hand (never committed):
```
LLM_PROVIDER=groq
GROQ_API_KEY=...
GEMINI_API_KEY=...
GNANI_API_KEY=...
# optional: GNANI_TTS_VOICE=Shivani
```
Check: `.venv\Scripts\python tests\test_verify.py` should end with "ALL AS EXPECTED". Then run (two windows):
```powershell
cd D:\gnani\backend; ..\.venv\Scripts\Activate.ps1; uvicorn main:app --reload --port 8000
cd D:\gnani\sim; npm run dev        # open http://localhost:5173
```
`http://localhost:8000/health` should show `arduino_cli`, `node`, the LLM, and `"gnani_key_set": true`. The Servo library comes with the repo (`arduino/libraries/Servo`); nothing else is installed by hand. A different project folder works too (all paths are relative), except `tests/drive.mjs`, which expects Edge at its default path.

## 6. Folder layout

```
D:\gnani\
  CLAUDE.md   WORKFLOW.md   THIRD_PARTY_NOTICES.md   .gitignore   .env (never commit)
  blink\blink.ino                 manual test sketch (keep)
  out\                            manual test output (ignore)
  backend\
    main.py                       FastAPI app and routes
    board_uno.json                Uno pin map (Phase 2; shared with verify.mjs and the browser)
    compiler.py                   arduino-cli wrapper
    llm.py                        one function that calls the LLM provider (Phase 2)
    gnani.py                      Prisma and Timbre calls (Phase 4)
    graph.py                      LangGraph nodes and edges (Phase 2+)
    evaluator.py                  checks vs measurements, pure Python (Phase 3)
    db.py                         SQLite helpers (Phase 2+)
    verify\verify.mjs             headless avr8js run (Phase 3), with its own package.json
    requirements.txt
  sim\                            Vite vanilla project
    index.html   main.js   simulator.js   circuit.js (parts, wiring)   signals.js (graph)   voice.js (mic to WAV)
  arduino\libraries\Servo\        Servo 1.3.0 (LGPL-2.1), vendored
  tests\                          offline tests, live agent test, headless-Edge UI driver (see MEMORY.md)
  MEMORY.md                       handoff notes for the next session
```
Phase 1 may keep everything in `backend/main.py`; split into the files above as phases add code. Create `.gitignore` early with: `.env`, `.venv/`, `node_modules/`, `out/`, `gnani.db`, `__pycache__/`.

## 7. Architecture in short (full detail in WORKFLOW.md)

```
Voice or text -> Prisma (if voice) -> FastAPI POST /run (streams progress)
  LangGraph: plan_and_code (LLM) -> preflight -> compile -> simulate+evaluate -> explain
             any failure -> fix (LLM, with the error or measurements) -> back to compile
             capped retries, honest give_up
  Response: circuit, code, hex, check report, explanation, spoken summary
Browser: draws the circuit (wokwi-elements), runs the hex live in avr8js, shows a signal graph
         plays Timbre's spoken summary
```
Key rules: **the circuit spec owns the pin numbers** (the backend writes `#define` lines into the sketch from it), and **measurements, not the LLM, decide success**.

## 8. Phases

Build in order. Each phase must run end to end before the next starts.

### Phase 1: Blinking LED in the browser (no AI)
**Backend** (`backend/main.py`): FastAPI with CORS open for localhost. `POST /compile` takes `{ "code": "..." }`, writes `sketch/sketch.ino` into a temp directory (folder name equals file name), runs `arduino-cli compile --fqbn arduino:avr:uno --output-dir <tmp>/out <tmp>/sketch` with `subprocess.run([...], capture_output=True, text=True, timeout=60)`, and returns `{ "ok": true, "hex": "<sketch.ino.hex text>" }` or `{ "ok": false, "error": "<stderr>" }`. Run with `uvicorn main:app --reload --port 8000` from `backend/` (inside the venv).

**Frontend** (`sim/`): `npm create vite@latest sim -- --template vanilla` (if prompted, choose vanilla and JavaScript), then `npm install avr8js @wokwi/elements`. A page with `<wokwi-led>` (pin 13), a textarea of Arduino code, a **Compile & Run** button, and a line showing compile time. `import '@wokwi/elements'` registers the components.

**avr8js essentials** (if names differ in the installed version, follow its README):
- Program memory: `new Uint16Array(16384)`; parse the Intel HEX text into `new Uint8Array(program.buffer)`.
- `new CPU(program)`.
- **`new AVRTimer(cpu, timer0Config)` is required.** Without it `delay()` never returns and the LED stays solid.
- `new AVRIOPort(cpu, portBConfig)` covers pins 8 to 13. **Pin 13 is PB5**, so read `portB.pinState(5) === PinState.High` inside `portB.addListener(...)` and set `led.value`.
- Run in `requestAnimationFrame`: about 250,000 iterations of `avrInstruction(cpu); cpu.tick();` per frame (16 MHz at 60 fps is about 266,000 cycles per frame).
- Keep a run counter so an older simulation loop stops when a new run starts.
- Intel HEX loader (data records, type `00`):
```js
function loadHex(source, target) {
  for (const line of source.split('\n')) {
    if (line[0] === ':' && line.substr(7, 2) === '00') {
      const n = parseInt(line.substr(1, 2), 16);
      const addr = parseInt(line.substr(3, 4), 16);
      for (let i = 0; i < n; i++)
        target[addr + i] = parseInt(line.substr(9 + i * 2, 2), 16);
    }
  }
}
```
**Housekeeping:** `git init`, `.gitignore`, `requirements.txt`, `THIRD_PARTY_NOTICES.md`.
**Done when:** the button makes the LED blink every 500 ms; changing `500` to `100` and clicking again blinks faster.

### Phase 2: The agent, text only
- Add `board_uno.json`, `llm.py`, `db.py`, `graph.py`, `compiler.py` (see `WORKFLOW.md` sections 4 to 6).
- Nodes: `plan_and_code`, `preflight`, `compile`, `fix`, `respond`. Conditional edges loop `fix` back to `compile` (max 3 attempts). SQLite checkpointing so a follow-up request in the same session sees the current circuit and code.
- Backend writes `#define` pin lines into the sketch from the circuit spec.
- `POST /run` takes `{session_id, text, circuit}` and returns circuit, code, hex. Streaming progress comes in Phase 3.
- Frontend: a text box for the request, a status line, the generated code shown in the textarea, and the LED simulation running it.
- Dev-only switch that **injects a deliberate compile error on attempt 1** so the fix loop can be tested (and later shown in the demo).
- Before writing the first LLM call, ask the user which LLM provider to use and for its API format.
**Done when:** typing "make the LED blink fast" produces working code that runs in the browser; with the dev switch on, the first attempt fails to compile and the agent fixes it by itself.

### Phase 3: Verification, explanation, more parts
- `backend/verify/verify.mjs` (own `package.json`, `npm install avr8js`) and `evaluator.py`. Check types `steady`, `blink`, `breathe` first, then `reacts_to_input`, then `servo_angle` (see `WORKFLOW.md` section 8).
- `explain` node and the explanation panel (summary, block-by-block walkthrough, what was verified, what was fixed, suggested next commands).
- `POST /run` streams Server-Sent Events: planning, preflight, compiling (attempt n), verifying, fixing (short reason), explaining, done.
- Circuit panel: add or remove LED, button, servo with a pin dropdown, drawn with wokwi-elements, with a label such as "LED1 on pin 9" (real wire drawing is not needed). Servo shown with `<wokwi-servo>` from the measured pulse width. Buzzer later if time allows.
- Live signal graph (`<canvas>`) of the pin values.
- **Test early:** (a) a few simulated seconds in Node runs fast enough; (b) PWM on pin 9 reads correctly (Timer1); (c) Servo library works under avr8js (Timer1 interrupts).
**Done when:** "make the LED on pin 9 breathe" is verified by measurement, a deliberately wrong version fails the check and gets fixed, and the explanation appears.

### Phase 4: Voice
- `gnani.py` for Prisma and Timbre, written only after the user supplies the docs. Check supported languages, accepted audio formats, latency and voice options. If WAV is required, convert in the browser instead of installing ffmpeg.
- Browser: push-to-talk button using `MediaRecorder`; a transcript line showing what was heard; Timbre plays the `spoken_summary` (never read code aloud).
- Voice rewiring: "connect a servo to pin 6" updates the circuit spec and redraws it.
- Test with short clips to save credits.
**Done when:** a spoken request goes through the full loop and the agent answers aloud.

### Phase 5: Polish and demo
- Clean simple UI: circuit area, signal graph, transcript, progress status, code with explanation.
- 2 or 3 reliable demo commands (for example: breathe an LED, then "when I press the button blink fast", then "slower"). **Include a run that fails a check first and fixes itself on screen.**
- Record 60 to 90 seconds: open on an empty board, speak the build into existence, show the self-correction, end with "Try it" to invite comments. Post on LinkedIn or X with `#GnaniAI #GreatIndianAIInternshipChallenge`, credit avr8js and wokwi-elements, and nominate the post as the contest asks.

### Schedule (today is Nov 7)
- Nov 7: Phase 1 done, Phase 2 started
- Nov 8: Phases 2 and 3
- Nov 9: Phase 4 and polish. **Stop building by the end of this day.**
- Nov 10: record, post and submit with buffer; do not leave it to the last hours.

## 9. After the demo video (not before)
- **BYOK:** a field where visitors enter their own Gnani key, held only in the browser and sent per request, never stored or logged by the backend. Prepare for it now by keeping every Gnani call behind `gnani.py`.
- **Deployment (optional):** free hosts give little CPU and RAM, sleep after idle, and wipe the filesystem on restart, so compile and simulation will be slower and SQLite data will be lost. The app needs Python, Node and arduino-cli with the `arduino:avr` core installed at build time, so it would be a Docker image plus a static host for `sim/`. Read secrets from environment variables, add a rate limit or passcode, and note that browsers only allow the microphone over HTTPS. Write the `Dockerfile` only when the user asks.

## 10. Risks, cut-line and status

**Risks**
- LLM latency and reliability: require strict JSON, keep prompts short, send the current circuit and code with each request.
- Evon access (section 2) and Gnani API formats are unconfirmed.
- avr8js models only the ATmega328P and peripherals are partial: verify each part before promising it.
- Credits: no loops that call Prisma, Timbre or the LLM repeatedly while testing; each is called once per request.
- Schedule is tight.

**Cut-line if behind on Nov 9 morning:** cut `install_lib`, `reacts_to_input`, buzzer, history UI, and voice rewiring. **Never cut:** the verify loop, real Prisma and Timbre use, the explanation, and the demo video.

**Status** (update at the end of each phase)

| Item | Status |
|---|---|
| Tools installed (Node, Python, Git, arduino-cli, `arduino:avr` core) | Done and verified |
| Manual blink compile (about 1.8 s) | Done |
| Phase 1 | Done (Nov 7): `/compile` + `/health`, browser blinks pin 13 in real time; venv is Python 3.12.10 |
| Phase 2 | Done (Nov 7): LangGraph loop plan_and_code, preflight, compile, fix, give_up, respond; SQLite checkpoint follow-ups; dev switch `debug_break`; tested in EN/HI/KN/Hinglish/Kanglish |
| Phase 3 | Done (Nov 8): headless verify + evaluator, explain node, SSE streaming, circuit panel (Uno, LED, button, servo, buzzer, potentiometer, pin dropdowns), live signal graph, dev modes `compile` and `timing` |
| Phase 4 | Done (Nov 8): `gnani.py`, `/transcribe`, `/speak`, browser WAV recorder (`sim/voice.js`), mic button, transcript line, spoken replies. Live-tested: Prisma exact on English clip (0.5-3.4 s), Timbre EN/HI/KN MP3s (2.6-5.4 s), full voice loop verified in headless Edge with a fake mic. Still to do by the user: real-mic test and listening to Hindi/Kannada quality (`out/voice-test/*.mp3`) |
| Phase 5 | Next: polish and demo |

Phase 4 notes (from docs.gnani.ai, Nov 8):
- Auth header `X-API-Key-ID` for both. Base `https://api.vachana.ai`.
- Prisma REST: `POST /stt/v3`, multipart `audio_file` (wav/mp3/ogg/flac/aac/m4a, max 30 s; NOT webm, so the browser records PCM and encodes 16 kHz WAV), `model=prisma-v3.0`, `language_code=en-IN,hi-IN,kn-IN` (up to 4 hints), `format=codemix` (native script + English words in Latin). Response `transcript`, `detected_language_code`.
- Timbre REST: `POST /api/v1/tts/inference` JSON `{text, model: timbre-v3.0, voice, language, speed, audio_config}`; v3.0 voices (Shivani, Avinash, Lakshmi, ...) speak all 10 languages. Languages `en-IN`, `hi-IN`, `kn-IN`, `hi-en`, `auto` (auto = by script, so Latin Hinglish would be read as English). We send Hindi/Hinglish in Devanagari and Kannada/Kanglish in Kannada script, numbers as words, language picked from the script.
- Accounts may be limited to 1 active request at a time (429 RATE_LIMITED, no Retry-After).

Phase 3 notes:
- Servo library 1.3.0 (LGPL-2.1) lives in `arduino/libraries/Servo`; `compiler.py` passes `--libraries` so nothing goes to Documents\Arduino.
- avr8js does not model pull-ups on input reads: buttons must be driven HIGH (released) with `setPin(bit, true)`.
- Measured: PWM 490 Hz on pin 9, tone exact, servo 0/90/180 exact, pot via `adc.channelValues[ch] = volts`. 4 s simulated in about 1.5 s.
- Dev switch `debug_break`: `"compile"` (missing semicolon) or `"timing"` (delays and timing constants x4, so the measured check fails and the agent fixes it). Use `"timing"` for the demo's on-screen self-correction.
- Known LLM habits now guarded in preflight: impossible thresholds, check windows that span a button press, swapped window bounds; `fix` uses a checks-only prompt when the checks are at fault. A fix can still bend code to a slightly wrong check (seen once: servo mapped 30-150 instead of 0-180).
- Browser testing: headless Edge driven over the DevTools protocol (script kept outside the repo).
- Not built: 7-segment display (optional), LCD, DC motor.
- Added after Phase 3 (Nov 8): drawn wiring (SVG over the circuit, using each wokwi element's `pinInfo`; visual only, the circuit spec stays the truth). Agent rewires are never silent: the backend diffs the user's circuit with the result (`circuit_changes`), the UI shows a notice and outlines the moved part, and explain mentions it. Preflight moves an LED that must fade to a free PWM pin by itself (`move_to_pwm`), avoiding 9/10 with Servo and 3/11 with tone().

LLM decision: Evon is weights-only (too heavy locally). `llm.py` uses OpenAI-compatible endpoints: `LLM_PROVIDER=groq` (default model `openai/gpt-oss-120b`, about 1.1 s per call) or `gemini` (`gemini-3.5-flash`; the `gemini-flash-latest` alias returned 503 "high demand").

Phase 2 notes for Phase 3:
- (Resolved in Phase 3) The `arduino:avr` core does not include Servo; it is vendored in `arduino/libraries/Servo` and passed with `--libraries`.
- The model often writes blink code whose toggle interval equals the full period it promised in `checks` (toggling every 2 s = a 4 s cycle vs a 1.5-2.5 s check). The verifier should catch this; good natural self-correction for the demo.
- `fix` always routes back through `preflight` (instant) instead of straight to `compile`.

Phase 1 notes for later phases:
- avr8js's ESM build cannot be loaded by plain Node (extensionless imports). In `verify.mjs` use `import ... from 'avr8js'` (bare name resolves to the CommonJS build), which works.
- Headless speed: 3 simulated seconds take about 1.4 to 1.5 s wall time (blink sketch), so `duration_ms` 4000 to 8000 costs roughly 2 to 4 s per verify.
- `sim/simulator.js` `createAvr(hex)` has no browser code, so Node can reuse the same setup.
