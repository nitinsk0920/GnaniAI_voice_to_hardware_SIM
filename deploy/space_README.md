---
title: Voice-to-Hardware Simulator
emoji: 🔌
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Talk to an AI agent that builds and verifies Arduino code
---

# Voice-to-Hardware Simulator

Design a virtual Arduino circuit, then tell an AI agent (by voice or text, in English, Hindi or
Kannada) what the hardware should do. The agent writes the Arduino code, compiles it, runs it in a
simulator, **measures the pins to prove it works**, fixes itself when a check fails, and explains
every line. Live mode is a hands-free voice conversation.

**Bring your own keys:** click **🔑 API keys** and add a Groq (or Gemini) key and a Gnani key.
They stay in your browser and are sent only with your own requests; the server never stores or logs them.

Voice by Gnani AI **Prisma** (speech-to-text) and **Timbre** (text-to-speech).
Built on [avr8js](https://github.com/wokwi/avr8js) and [wokwi-elements](https://github.com/wokwi/wokwi-elements) by Uri Shaked (MIT).

Entry for the Great Indian AI Internship Challenge by Gnani AI. #GnaniAI #GreatIndianAIInternshipChallenge
