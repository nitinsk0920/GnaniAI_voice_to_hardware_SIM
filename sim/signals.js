// Pin history for the live view: a list of [simMs, level] changes per pin.
// Used for the signal graph, LED brightness (PWM duty), servo angle and buzzer pitch.

const KEEP_MS = 4000;
let history = {};      // pin -> [[ms, 0|1], ...]
let pulseWidth = {};   // pin -> last complete HIGH pulse in ms (servo signal)
let lastRise = {};

export function resetSignals() {
  history = {};
  pulseWidth = {};
  lastRise = {};
}

export function record(pin, high, ms) {
  (history[pin] ??= []).push([ms, high ? 1 : 0]);
  if (high) lastRise[pin] = ms;
  else if (lastRise[pin] !== undefined) pulseWidth[pin] = ms - lastRise[pin];
}

// Drop old changes, but keep the last one before the cut so the level there is known.
export function prune(nowMs) {
  for (const edges of Object.values(history)) {
    let i = 0;
    while (i + 1 < edges.length && edges[i + 1][0] < nowMs - KEEP_MS) i++;
    if (i) edges.splice(0, i);
  }
}

export const lastPulseMs = (pin) => pulseWidth[pin];

// Fraction of time HIGH in n equal slices of [t0, t1], in one pass over the edges.
export function dutySlices(pin, t0, t1, n) {
  const edges = history[pin] || [];
  const out = new Array(n).fill(0);
  const step = (t1 - t0) / n;
  let level = 0;
  let i = 0;
  while (i < edges.length && edges[i][0] <= t0) level = edges[i++][1];
  for (let k = 0; k < n; k++) {
    const a = t0 + k * step;
    const b = a + step;
    let t = a;
    let high = 0;
    while (i < edges.length && edges[i][0] < b) {
      if (level) high += edges[i][0] - t;
      t = edges[i][0];
      level = edges[i][1];
      i++;
    }
    if (level) high += b - t;
    out[k] = high / step;
  }
  return out;
}

export const duty = (pin, t0, t1) => dutySlices(pin, t0, t1, 1)[0];

// Frequency in Hz from rising edges in the last windowMs.
export function frequency(pin, nowMs, windowMs = 100) {
  const edges = history[pin] || [];
  let rises = 0;
  for (let i = edges.length - 1; i >= 0 && edges[i][0] >= nowMs - windowMs; i--) if (edges[i][1]) rises++;
  return rises >= 3 ? (rises * 1000) / windowMs : 0;
}

// Draw one row per signal for the last spanMs. PWM shows as a smooth brightness curve
// because each pixel column shows the fraction of time the pin was HIGH.
export function drawGraph(canvas, rows, nowMs, spanMs = 3000) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const rowH = 34;
  const height = Math.max(1, rows.length) * rowH + 18;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(canvas);
  ctx.clearRect(0, 0, width, height);

  const labelW = 92;
  const plotW = Math.max(10, width - labelW - 8);
  const cols = Math.floor(plotW);
  ctx.font = '12px system-ui, sans-serif';
  ctx.textBaseline = 'middle';

  rows.forEach((row, r) => {
    const top = r * rowH + 4;
    const h = rowH - 10;
    ctx.fillStyle = css.getPropertyValue('--muted');
    ctx.fillText(row.label, 0, top + h / 2);
    ctx.strokeStyle = css.getPropertyValue('--grid');
    ctx.strokeRect(labelW + 0.5, top + 0.5, plotW, h);

    const slices = dutySlices(row.pin, nowMs - spanMs, nowMs, cols);
    ctx.beginPath();
    ctx.moveTo(labelW, top + h);
    slices.forEach((d, x) => ctx.lineTo(labelW + x, top + h - d * h));
    ctx.lineTo(labelW + cols, top + h);
    ctx.closePath();
    ctx.fillStyle = row.color + '55';
    ctx.fill();
    ctx.strokeStyle = row.color;
    ctx.beginPath();
    slices.forEach((d, x) => (x ? ctx.lineTo : ctx.moveTo).call(ctx, labelW + x, top + h - d * h));
    ctx.stroke();
  });

  ctx.fillStyle = css.getPropertyValue('--muted');
  ctx.fillText(`last ${spanMs / 1000} s of simulated time`, labelW, rows.length * rowH + 8);
}
