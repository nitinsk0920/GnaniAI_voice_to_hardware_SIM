// Runs compiled Arduino Uno code (Intel HEX text) with avr8js.
//
// Plain-language overview:
// - The Uno's chip (ATmega328P) has 32 KB of program memory. We load the hex into it.
// - CPU executes one instruction at a time; cpu.tick() lets timers and other peripherals react.
// - Timer0 is what makes delay() and millis() work. Without it delay() never returns.
//   Timer1 drives PWM on pins 9/10 and the Servo library; Timer2 drives tone() and PWM on 3/11.
// - Pins are grouped into "ports". Digital pins 8-13 are PORTB bits 0-5, so pin 13 is PORTB bit 5.
// - The ADC turns a voltage on A0-A5 into the 0-1023 number analogRead() returns.
import {
  CPU, avrInstruction, AVRTimer, AVRIOPort, AVRADC, PinState,
  timer0Config, timer1Config, timer2Config,
  portBConfig, portCConfig, portDConfig, adcConfig,
} from 'avr8js';

export { PinState };
export const CPU_HZ = 16_000_000; // Uno clock: 16 MHz

// Copy the data records (type 00) of an Intel HEX file into a byte array.
export function loadHex(source, target) {
  for (const line of source.split('\n')) {
    if (line[0] === ':' && line.substr(7, 2) === '00') {
      const n = parseInt(line.substr(1, 2), 16);
      const addr = parseInt(line.substr(3, 4), 16);
      for (let i = 0; i < n; i++) target[addr + i] = parseInt(line.substr(9 + i * 2, 2), 16);
    }
  }
}

// Build a CPU with timers, ports and the ADC. No browser code here, so Node can use it too.
export function createAvr(hex) {
  const program = new Uint16Array(16384); // 32 KB as 16-bit words
  loadHex(hex, new Uint8Array(program.buffer));
  const cpu = new CPU(program);
  // Keep references so the timers stay alive (they hook themselves into the CPU).
  const timers = [timer0Config, timer1Config, timer2Config].map((cfg) => new AVRTimer(cpu, cfg));
  const portB = new AVRIOPort(cpu, portBConfig);
  const portC = new AVRIOPort(cpu, portCConfig);
  const portD = new AVRIOPort(cpu, portDConfig);
  const adc = new AVRADC(cpu, adcConfig); // analog inputs: set adc.channelValues[n] in volts

  // Run until the CPU has used this many more clock cycles.
  function runCycles(cycles) {
    const end = cpu.cycles + cycles;
    while (cpu.cycles < end) {
      avrInstruction(cpu);
      cpu.tick();
    }
  }

  return { cpu, timers, portB, portC, portD, adc, runCycles };
}

// Start a live simulation in the browser.
// watch:  [{pin, port: "B"|"C"|"D", bit}] output pins to report (positions from backend/boards/*.json)
// inputs: [{port, bit, level}] buttons; avr8js does not model pull-ups, so a released button is driven HIGH
// analog: [{channel, volts}] potentiometers
// onPin(pin, isHigh, simMs) fires on every change; onTime(simMs) once per frame.
// Returns {setPin(port, bit, level), setAnalog(channel, volts)} for clicks and knob turns.
// Starting a new run stops the previous one (run counter).
let currentRun = 0;

export function startSimulation(hex, { watch = [], inputs = [], analog = [], onPin, onTime } = {}) {
  const runId = ++currentRun;
  const avr = createAvr(hex);
  const ports = { B: avr.portB, C: avr.portC, D: avr.portD };
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
