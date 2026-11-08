// Runs compiled Arduino code (Intel HEX text) with avr8js, for the Uno/Nano (ATmega328P) or Mega (ATmega2560).
//
// Plain-language overview:
// - The chip's program memory (32 KB on the Uno, 256 KB on the Mega) is filled from the hex file.
// - CPU executes one instruction at a time; cpu.tick() lets timers and other peripherals react.
// - Timer0 is what makes delay() and millis() work. Without it delay() never returns.
//   The other timers drive PWM (analogWrite), the Servo library and tone().
// - Pins are grouped into "ports". On the Uno, pins 8-13 are PORTB bits 0-5, so pin 13 is PORTB bit 5.
//   The board profile (backend/boards/*.json) says which port and bit each pin uses.
// - The ADC turns a voltage on an analog pin into the 0-1023 number analogRead() returns.
// The per-chip setup (timers, ports, ADC) lives in backend/boards/avr_chips.mjs, shared with the verifier.
import * as avr8 from 'avr8js';
import { chipSetup, loadHex } from '../backend/boards/avr_chips.mjs';

const { CPU, avrInstruction, AVRTimer, AVRIOPort, AVRADC, PinState } = avr8;
export { PinState, loadHex };
export const CPU_HZ = 16_000_000; // 16 MHz on all three boards

// Build a CPU with timers, ports and the ADC. No browser code here, so Node can use it too.
// mcu: "atmega328p" (Uno, Nano) or "atmega2560" (Mega).
export function createAvr(hex, mcu = 'atmega328p') {
  const chip = chipSetup(avr8, mcu);
  const program = new Uint16Array(chip.progBytes / 2);
  loadHex(hex, new Uint8Array(program.buffer));
  const cpu = new CPU(program, chip.sramBytes);
  // Keep references so the timers stay alive (they hook themselves into the CPU).
  const timers = chip.timers.map((cfg) => new AVRTimer(cpu, cfg));
  const ports = Object.fromEntries(Object.entries(chip.ports).map(([l, cfg]) => [l, new AVRIOPort(cpu, cfg)]));
  const { B: portB, C: portC, D: portD } = ports;
  const adc = new AVRADC(cpu, chip.adc); // analog inputs: set adc.channelValues[n] in volts

  // Run until the CPU has used this many more clock cycles.
  function runCycles(cycles) {
    const end = cpu.cycles + cycles;
    while (cpu.cycles < end) {
      avrInstruction(cpu);
      cpu.tick();
    }
  }

  return { cpu, timers, ports, portB, portC, portD, adc, runCycles };
}

// Start a live simulation in the browser.
// mcu:    chip of the selected board (board profile "mcu")
// watch:  [{pin, port: "B"|"C"|..., bit}] output pins to report (positions from backend/boards/*.json)
// inputs: [{port, bit, level}] buttons; avr8js does not model pull-ups, so a released button is driven HIGH
// analog: [{channel, volts}] potentiometers
// onPin(pin, isHigh, simMs) fires on every change; onTime(simMs) once per frame.
// Returns {setPin(port, bit, level), setAnalog(channel, volts)} for clicks and knob turns.
// Starting a new run stops the previous one (run counter).
let currentRun = 0;

export function startSimulation(hex, { mcu, watch = [], inputs = [], analog = [], onPin, onTime } = {}) {
  const runId = ++currentRun;
  const avr = createAvr(hex, mcu);
  const ports = avr.ports;
  const simMs = () => avr.cpu.cycles / (CPU_HZ / 1000);

  for (const i of inputs) ports[i.port].setPin(i.bit, i.level);
  for (const a of analog) avr.adc.channelValues[a.channel] = a.volts;

  // A port listener fires when any of its 8 pins changes, so check each watched pin on it.
  const lastLevel = {}; // pin -> last reported level, so we only report real changes
  for (const [name, port] of Object.entries(ports)) {
    const pinsHere = watch.filter((w) => w.port === name);
    if (!pinsHere.length) continue;
    port.addListener(() => {
      if (runId !== currentRun) return;
      for (const w of pinsHere) {
        const high = port.pinState(w.bit) === PinState.High;
        if (lastLevel[w.pin] !== high) {
          lastLevel[w.pin] = high;
          onPin?.(w.pin, high, simMs());
        }
      }
    });
  }

  // Advance the chip by the real time that passed since the last frame,
  // so it runs at true speed on any screen refresh rate.
  // Capped at 50 ms per frame so a background tab does not freeze the page on return.
  let last = performance.now();
  function frame(now) {
    if (runId !== currentRun) return; // a newer run started: stop this loop
    const elapsedMs = Math.min(now - last, 50);
    last = now;
    avr.runCycles(Math.round((elapsedMs / 1000) * CPU_HZ));
    onTime?.(simMs());
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    setPin: (port, bit, level) => { if (runId === currentRun) ports[port].setPin(bit, level); },
    setAnalog: (channel, volts) => { avr.adc.channelValues[channel] = volts; },
    simMs,
  };
}

export function stopSimulation() {
  currentRun++;
}
