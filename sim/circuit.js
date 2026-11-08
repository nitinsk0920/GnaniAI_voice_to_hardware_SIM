// Circuit panel: draws the Uno and the parts, lets the user add/remove parts and pick pins,
// and connects each part to the running simulation (clicks, knob, servo angle, buzzer sound).
import board from '../backend/board_uno.json'; // shared pin map: pin -> port, bit, pwm
import { record, duty, lastPulseMs, frequency } from './signals.js';

const DIGITAL = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13];
const ANALOG = ['A0', 'A1', 'A2', 'A3', 'A4', 'A5'];
export const PART_TYPES = {
  led:           { label: 'LED',           prefix: 'LED',    pins: DIGITAL, prefer: [13, 9, 10, 11, 12, 8, 7, 6, 5, 4, 3, 2] },
  button:        { label: 'Push button',   prefix: 'BUTTON', pins: [...DIGITAL, ...ANALOG], prefer: [2, 3, 4, 7, 8, 12] },
  servo:         { label: 'Servo',         prefix: 'SERVO',  pins: DIGITAL, prefer: [6, 5, 3, 10, 9, 11] },
  buzzer:        { label: 'Buzzer',        prefix: 'BUZZER', pins: DIGITAL, prefer: [8, 7, 4, 12] },
  potentiometer: { label: 'Potentiometer', prefix: 'POT',    pins: ANALOG,  prefer: ANALOG },
};
const LED_COLORS = ['red', 'green', 'blue', 'yellow', 'orange', 'white'];
const GRAPH_COLORS = ['#d9480f', '#2b8a3e', '#1971c2', '#ae3ec9', '#e67700', '#0c8599', '#c2255c'];
const SERVO_MIN_US = 544;
const SERVO_MAX_US = 2400; // Servo library defaults

let circuit = { components: [] };
let parts = {};          // id -> {c, el}
let controller = null;   // from startSimulation: setPin, setAnalog, simMs
let onChange = () => {};
let ui = {};
let audio = null;        // Web Audio square wave for the buzzer
let soundOn = true;

const pinInfo = (pin) => board.pins[String(pin)];

// ---------- define block (same format the backend writes) ----------

export function defineBlock(c) {
  return '// Pins from your circuit (written by the backend)\n'
    + c.components.map((p) => `#define ${p.id}_PIN ${p.pin}`).join('\n') + '\n\n';
}

export function replaceDefineBlock(code, c) {
  const block = /^\/\/ Pins from your circuit.*\n(#define \w+_PIN .*\n)*\n?/m;
  return block.test(code) ? code.replace(block, defineBlock(c)) : defineBlock(c) + code;
}

// ---------- drawing and editing ----------

export function initCircuit({ partsEl, addTypeEl, addBtn, unoEl, stageEl, boardEl, wiresEl, onCircuitChange }) {
  ui = { partsEl, addTypeEl, unoEl, stageEl, boardEl, wiresEl };
  onChange = onCircuitChange;
  for (const [type, info] of Object.entries(PART_TYPES)) addTypeEl.append(new Option(info.label, type));
  addBtn.addEventListener('click', () => addPart(addTypeEl.value));
  new ResizeObserver(() => drawWires()).observe(stageEl);
}

export function getCircuit() {
  return structuredClone(circuit);
}

// Returns the ids of parts that are new or moved compared with the current circuit,
// and outlines them for a few seconds so an agent rewire is never silent.
export function setCircuit(c, { highlight = false } = {}) {
  const before = Object.fromEntries(circuit.components.map((p) => [p.id, String(p.pin)]));
  circuit = structuredClone(c);
  draw();
  const changedIds = circuit.components.filter((p) => before[p.id] !== String(p.pin)).map((p) => p.id);
  if (highlight) {
    for (const id of changedIds) {
      const card = parts[id]?.el.closest('.part');
      card?.classList.add('changed');
      setTimeout(() => card?.classList.remove('changed'), 6000);
    }
  }
  return changedIds;
}

// ---------- wires (drawing only; the circuit spec is what the simulator uses) ----------

const SIG = 'SIG';
const PART_WIRES = { // [part pin name, Uno pin name or SIG for the part's own Arduino pin]
  led:           [['A', SIG], ['C', 'GND.1']],
  button:        [['1.l', SIG], ['2.r', 'GND.1']],
  servo:         [['PWM', SIG], ['V+', '5V'], ['GND', 'GND.1']],
  buzzer:        [['2', SIG], ['1', 'GND.1']],
  potentiometer: [['SIG', SIG], ['VCC', '5V'], ['GND', 'GND.1']],
};
const LANE = 6; // px between parallel wires

// Position of a named pin of a wokwi element, in stage coordinates (handles CSS scaling).
function pinPos(el, name, stageRect) {
  const info = (el.pinInfo || []).find((p) => p.name === name);
  if (!info) return null;
  const r = el.getBoundingClientRect();
  const scale = el.offsetWidth ? r.width / el.offsetWidth : 1;
  return { x: r.left - stageRect.left + info.x * scale, y: r.top - stageRect.top + info.y * scale,
    side: info.x < 2 ? 'left' : info.x > el.offsetWidth - 2 ? 'right' : 'bottom' };
}

function drawWires() {
  const { stageEl, wiresEl, unoEl, boardEl } = ui;
  if (!stageEl || !unoEl.pinInfo) return;
  const wires = [];
  circuit.components.forEach((c, i) => {
    const el = parts[c.id]?.el;
    for (const [partPin, unoPin] of PART_WIRES[c.type] || []) {
      const name = unoPin === SIG ? String(c.pin) : unoPin;
      const color = unoPin === SIG ? GRAPH_COLORS[i % GRAPH_COLORS.length] : name === '5V' ? '#d6336c' : '#222';
      wires.push({ el, partPin, unoPin: name, color });
    }
  });

  // Room above the board for one horizontal lane per wire.
  boardEl.style.marginTop = `${20 + wires.length * LANE}px`;
  const stage = stageEl.getBoundingClientRect();
  wiresEl.setAttribute('width', stage.width);
  wiresEl.setAttribute('height', stage.height);
  const board = unoEl.getBoundingClientRect();
  const boardTop = board.top - stage.top;
  const boardRight = board.right - stage.left;
  const boardBottom = board.bottom - stage.top;

  // Resolve both ends, then give the shortest wires the lanes nearest the board (fewer crossings).
  const resolved = wires
    .map((w) => ({ ...w, from: pinPos(unoEl, w.unoPin, stage), to: w.el && pinPos(w.el, w.partPin, stage) }))
    .filter((w) => w.from && w.to)
    .sort((a, b) => Math.abs(a.to.x - a.from.x) - Math.abs(b.to.x - b.from.x));

  // Room under the board for the wires that go around it (5V and analog pins).
  const aroundCount = resolved.filter((w) => w.from.y > boardTop + board.height / 2).length;
  boardEl.style.marginBottom = `${aroundCount ? 14 + aroundCount * LANE : 0}px`;

  let rightLane = 0;
  const paths = resolved.map((w, k) => {
    const lane = boardTop - 12 - k * LANE;
    let d = `M${w.from.x},${w.from.y}`;
    if (w.from.y > boardTop + board.height / 2) {
      // Bottom header (5V, A0-A5): go down, around the right side of the board, then up.
      const below = boardBottom + 8 + rightLane * LANE;
      const side = boardRight + 10 + rightLane * LANE;
      rightLane++;
      d += ` V${below} H${side} V${lane}`;
    } else {
      d += ` V${lane}`;
    }
    const { x, y, side } = w.to;
    if (side === 'bottom') d += ` H${x} V${y}`;
    else d += ` H${side === 'left' ? x - 10 : x + 10} V${y} H${x}`;
    return `<path d="${d}" stroke="${w.color}" /><circle cx="${x}" cy="${y}" r="2.5" fill="${w.color}" />`
      + `<circle cx="${w.from.x}" cy="${w.from.y}" r="2.5" fill="${w.color}" />`;
  });
  wiresEl.innerHTML = paths.join('');
}

// Custom elements draw themselves a moment after insertion, so wait two frames before measuring.
function scheduleWires() {
  requestAnimationFrame(() => requestAnimationFrame(drawWires));
}

function changed() {
  draw();
  onChange(getCircuit());
}

function usedPins(exceptId) {
  return new Set(circuit.components.filter((p) => p.id !== exceptId).map((p) => String(p.pin)));
}

function addPart(type) {
  const info = PART_TYPES[type];
  const used = usedPins();
  const pin = [...info.prefer, ...info.pins].find((p) => !used.has(String(p)));
  if (pin === undefined) return alert(`No free pin left for a ${info.label}.`);
  let n = 1;
  while (circuit.components.some((p) => p.id === `${info.prefix}${n}`)) n++;
  circuit.components.push({ type, id: `${info.prefix}${n}`, pin });
  changed();
}

function makeElement(c, index) {
  const tag = { led: 'wokwi-led', button: 'wokwi-pushbutton', servo: 'wokwi-servo',
    buzzer: 'wokwi-buzzer', potentiometer: 'wokwi-potentiometer' }[c.type];
  const el = document.createElement(tag);
  if (c.type === 'led') el.setAttribute('color', LED_COLORS[index % LED_COLORS.length]);
  if (c.type === 'button') {
    el.setAttribute('color', 'green');
    el.addEventListener('button-press', () => pressButton(c, true));
    el.addEventListener('button-release', () => pressButton(c, false));
  }
  if (c.type === 'potentiometer') {
    el.value = c.value ?? 512;
    el.addEventListener('input', () => {
      c.value = Number(el.value);
      controller?.setAnalog(pinInfo(c.pin).bit, (c.value / 1023) * 5);
    });
  }
  return el;
}

function pressButton(c, pressed) {
  if (!controller) return;
  const { port, bit } = pinInfo(c.pin);
  controller.setPin(port, bit, !pressed); // wired to GND: pressed reads LOW
  record(c.pin, pressed, controller.simMs()); // graph shows "pressed" as high
}

function draw() {
  ui.partsEl.innerHTML = '';
  parts = {};
  circuit.components.forEach((c, i) => {
    const card = document.createElement('div');
    card.className = `part part-${c.type}`;
    const el = makeElement(c, i);
    parts[c.id] = { c, el };

    const row = document.createElement('div');
    row.className = 'part-label';
    const name = document.createElement('strong');
    name.textContent = c.id;
    const select = document.createElement('select');
    select.title = 'Pin';
    const used = usedPins(c.id);
    for (const p of PART_TYPES[c.type].pins) {
      const pwm = pinInfo(p).pwm ? ' ~' : '';
      const opt = new Option(`pin ${p}${pwm}`, p, false, String(p) === String(c.pin));
      opt.disabled = used.has(String(p));
      select.append(opt);
    }
    select.addEventListener('change', () => {
      c.pin = /^\d+$/.test(select.value) ? Number(select.value) : select.value;
      changed();
    });
    const remove = document.createElement('button');
    remove.className = 'remove';
    remove.title = `Remove ${c.id}`;
    remove.textContent = '×';
    remove.addEventListener('click', () => {
      circuit.components = circuit.components.filter((p) => p.id !== c.id);
      changed();
    });
    row.append(name, select, remove);

    const holder = document.createElement('div');
    holder.className = 'part-visual';
    holder.append(el);
    card.append(row, holder); // label on top, so the part's pins face the board and its wires
    ui.partsEl.append(card);
  });
  scheduleWires();
}

// ---------- connection to the running simulation ----------

export function setController(ctrl) {
  controller = ctrl;
  ui.unoEl.ledPower = !!ctrl;
}

// What the simulator should watch and drive for this circuit.
export function simSetup() {
  const outputs = circuit.components.filter((c) => ['led', 'servo', 'buzzer'].includes(c.type)).map((c) => c.pin);
  const watchPins = [...new Set([...outputs, 13])]; // 13 also drives the Uno's on-board LED
  return {
    watch: watchPins.map((pin) => ({ pin, ...pinInfo(pin) })),
    inputs: circuit.components.filter((c) => c.type === 'button').map((c) => ({ ...pinInfo(c.pin), level: true })),
    analog: circuit.components.filter((c) => c.type === 'potentiometer')
      .map((c) => ({ channel: pinInfo(c.pin).bit, volts: ((c.value ?? 512) / 1023) * 5 })),
  };
}

export function graphRows() {
  return circuit.components
    .filter((c) => c.type !== 'potentiometer')
    .map((c, i) => ({ pin: c.pin, label: `${c.id} · ${c.pin}`, color: GRAPH_COLORS[i % GRAPH_COLORS.length] }));
}

// Called every animation frame while running: show what the pins are doing.
export function updateParts(nowMs) {
  let toneHz = 0;
  for (const { c, el } of Object.values(parts)) {
    if (c.type === 'led') {
      const d = duty(c.pin, nowMs - 20, nowMs); // over 20 ms, PWM averages into brightness
      el.value = d > 0.01;
      el.brightness = Math.min(1, 0.15 + d);
    } else if (c.type === 'servo') {
      const w = lastPulseMs(c.pin);
      if (w >= 0.3 && w <= 3) {
        const angle = ((w * 1000 - SERVO_MIN_US) / (SERVO_MAX_US - SERVO_MIN_US)) * 180;
        el.angle = Math.max(0, Math.min(180, angle));
      }
    } else if (c.type === 'buzzer') {
      const f = frequency(c.pin, nowMs);
      el.hasSignal = f > 0;
      toneHz ||= f;
    }
  }
  ui.unoEl.led13 = duty(13, nowMs - 20, nowMs) > 0.5;
  setTone(toneHz);
}

export function resetParts() {
  for (const { c, el } of Object.values(parts)) {
    if (c.type === 'led') el.value = false;
    if (c.type === 'buzzer') el.hasSignal = false;
  }
  ui.unoEl.led13 = false;
  setController(null);
  setTone(0);
}

// ---------- buzzer sound ----------

// Browsers only allow sound after a click, so this is called from button handlers.
export function unlockAudio() {
  if (!audio) {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'square';
    gain.gain.value = 0;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    audio = { ctx, osc, gain };
  }
  audio.ctx.resume();
}

export function setSound(on) {
  soundOn = on;
  if (!on) setTone(0);
}

function setTone(hz) {
  if (!audio) return;
  const t = audio.ctx.currentTime;
  if (hz > 0 && soundOn) {
    audio.osc.frequency.setTargetAtTime(hz, t, 0.01);
    audio.gain.gain.setTargetAtTime(0.03, t, 0.01);
  } else {
    audio.gain.gain.setTargetAtTime(0, t, 0.01);
  }
}
