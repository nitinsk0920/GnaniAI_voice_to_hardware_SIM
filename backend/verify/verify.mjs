// Headless run of a compiled sketch: records every change on the watched pins.
//
// usage: node verify.mjs <hexfile> <configfile>
// config (written by graph.py):
// {
//   "duration_ms": 4000,
//   "watch":  [{"pin": 9, "port": "B", "bit": 1}],
//   "inputs": [{"pin": 2, "port": "D", "bit": 2, "level": true}],   // buttons: held HIGH = released
//   "analog": [{"channel": 0, "volts": 2.5}],                       // potentiometers
//   "events": [{"at_ms": 2000, "kind": "pin", "port": "D", "bit": 2, "level": false},
//              {"at_ms": 3000, "kind": "analog", "channel": 0, "volts": 5}]
// }
// prints: {"duration_ms": 4000, "wall_ms": 1800, "pins": {"9": [[0, 0], [0.51, 1], ...]}}
//
// Note: import 'avr8js' by its bare name (its ESM build cannot be loaded by plain Node).
import { readFileSync } from 'node:fs';
import * as avr from 'avr8js';
import { chipSetup, loadHex } from '../boards/avr_chips.mjs'; // shared with the browser simulator

const { CPU, avrInstruction, AVRTimer, AVRIOPort, AVRADC, PinState } = avr;
const CYCLES_PER_MS = 16000; // 16 MHz

const [, , hexPath, configPath] = process.argv;
const config = JSON.parse(readFileSync(configPath, 'utf8'));
const wallStart = performance.now();

// The chip comes from the board profile ("atmega328p" for Uno/Nano, "atmega2560" for Mega).
const chip = chipSetup(avr, config.mcu || 'atmega328p');
const program = new Uint16Array(chip.progBytes / 2);
loadHex(readFileSync(hexPath, 'utf8'), new Uint8Array(program.buffer));
const cpu = new CPU(program, chip.sramBytes);
const timers = chip.timers.map((c) => new AVRTimer(cpu, c));
const ports = Object.fromEntries(Object.entries(chip.ports).map(([letter, cfg]) => [letter, new AVRIOPort(cpu, cfg)]));
const adc = new AVRADC(cpu, chip.adc);

// Initial input levels. avr8js does not model pull-ups when reading, so released buttons are driven HIGH.
for (const i of config.inputs || []) ports[i.port].setPin(i.bit, i.level);
for (const a of config.analog || []) adc.channelValues[a.channel] = a.volts;

// Record pin changes. Times in ms with sub-millisecond precision.
const pins = {};
const lastLevel = {};
const nowMs = () => Math.round((cpu.cycles / CYCLES_PER_MS) * 1000) / 1000;
for (const w of config.watch || []) pins[w.pin] = [];
for (const [name, port] of Object.entries(ports)) {
  const watched = (config.watch || []).filter((w) => w.port === name);
  if (!watched.length) continue;
  const record = () => {
    for (const w of watched) {
      const level = port.pinState(w.bit) === PinState.High ? 1 : 0;
      if (lastLevel[w.pin] !== level) {
        lastLevel[w.pin] = level;
        pins[w.pin].push([nowMs(), level]);
      }
    }
  };
  record(); // level at time 0
  port.addListener(record);
}

function runUntil(ms) {
  const end = ms * CYCLES_PER_MS;
  while (cpu.cycles < end) {
    avrInstruction(cpu);
    cpu.tick();
  }
}

const events = [...(config.events || [])].sort((a, b) => a.at_ms - b.at_ms);
for (const e of events) {
  if (e.at_ms >= config.duration_ms) break;
  runUntil(e.at_ms);
  if (e.kind === 'pin') ports[e.port].setPin(e.bit, e.level);
  if (e.kind === 'analog') adc.channelValues[e.channel] = e.volts;
}
runUntil(config.duration_ms);

process.stdout.write(JSON.stringify({
  duration_ms: config.duration_ms,
  wall_ms: Math.round(performance.now() - wallStart),
  pins,
}));
void timers; // the timers hook themselves into the CPU; kept referenced on purpose
