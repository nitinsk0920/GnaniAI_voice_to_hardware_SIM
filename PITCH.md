# Voice-to-Hardware Simulator: solution pitch

**Try it:** https://voice-to-hardware.onrender.com · **Code:** https://github.com/nitinsk0920/GnaniAI_voice_to_hardware_SIM

## The problem
Learning electronics is hard to start. You need an Arduino board, parts, wires and C++ code, and
most learners in India are more comfortable speaking Hindi, Kannada or a mix with English than writing
code. AI assistants can write Arduino code, but **nobody checks whether it actually works**: the
learner finds out only after wiring real hardware, and the bug is theirs to find.

## Our solution
A voice-first Arduino lab in the browser. You place parts on a virtual Arduino (LED, button, servo,
buzzer, knob) and simply say what the hardware should do, for example
*"Jab main button dabaun, tab servo ko 90 degree pe le jao"*. An AI agent then:

1. writes the Arduino code for **your** circuit and pins,
2. compiles it with the real Arduino compiler,
3. runs it in a chip-level simulator and **measures the pins** (blink rate, brightness, servo angle, buzzer pitch),
4. **fixes its own code** when a measurement fails, and tries again,
5. explains the code **line by line** and **speaks** a short answer in your language.

The circuit then runs live on screen, with a signal graph of every pin.

## Use of Gnani AI
- **Prisma (speech-to-text):** push-to-talk, and real-time streaming in Live mode with server-side
  turn detection. Code-mixed speech keeps technical words in English ("LED", "servo").
- **Timbre (text-to-speech):** spoken replies in English, Hindi and Kannada; streaming playback starts
  in about 1.5 s.
- **Live mode:** a hands-free conversation like Gemini Live. The agent says what it is doing, says when
  a test fails and it is fixing it, and you can interrupt it by talking.

## What makes it different
- **Measured, not trusted:** success is decided by pin measurements in a simulator, never by the AI saying "done".
- **Self-correcting on real evidence:** a failed check sends the measured numbers back to the AI.
- **Your circuit is the truth:** pin numbers come from your drawing; any change the agent makes is shown, never silent.
- **Honest:** it refuses parts it cannot simulate instead of faking a pass.
- **Safe to share:** visitors bring their own API keys, which stay in their browser.

## Who it is for
Students, hobbyists and teachers without hardware at hand, and Indian-language speakers who want to
learn by talking instead of typing. The code is real Arduino code, ready to upload to a real board.

## Built with
Gnani Prisma and Timbre · FastAPI · LangGraph · arduino-cli · avr8js and wokwi-elements (open source,
Uri Shaked) · Vite. Supports Arduino Uno, Nano and Mega 2560.

**Next:** more boards (Raspberry Pi Pico), a full component library with real pin-to-pin wiring
(relays, motors, sensors, displays), and flashing real boards from the browser.
