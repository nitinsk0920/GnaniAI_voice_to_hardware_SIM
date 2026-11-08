// Drive headless Edge over the DevTools protocol: run a list of steps on http://localhost:5173,
// print console errors, save screenshots.
// usage (backend on :8000 and Vite on :5173 running): node tests/drive.mjs tests/ui_timing_bug.json out/ui
// Fake microphone: set EXTRA_ARGS="--use-fake-ui-for-media-stream|--use-fake-device-for-media-stream|--use-file-for-fake-audio-capture=C:\path\clip.wav"
// Use an output folder under out/ (git-ignored): it also holds a throwaway Edge profile.
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const [, , stepsFile, outArg] = process.argv;
const outDir = resolve(outArg); // Edge needs an absolute profile folder
const steps = JSON.parse(readFileSync(stepsFile, 'utf8'));
mkdirSync(outDir, { recursive: true });
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const PORT = 9333;
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(outDir, 'profile')}`,
  '--autoplay-policy=no-user-gesture-required', '--window-size=1400,1400',
  ...(process.env.EXTRA_ARGS ? process.env.EXTRA_ARGS.split('|') : []), 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let target;
for (let i = 0; i < 80 && !target; i++) {
  await sleep(250);
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === 'page'); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let id = 0;
const pending = new Map();
ws.addEventListener('message', (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(msg.params.type))
    console.log(`  console.${msg.params.type}:`, msg.params.args.map((a) => a.value ?? a.description).join(' '));
  if (msg.method === 'Runtime.exceptionThrown')
    console.log('  EXCEPTION:', msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error')
    console.log('  log error:', msg.params.entry.text, msg.params.entry.url || '');
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) return 'EVAL ERROR: ' + r.result.exceptionDetails.exception?.description;
  return r.result?.result?.value;
};

await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 1400, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: 'http://localhost:5173/' });
await sleep(2500);

for (const s of steps) {
  if (s.eval) { const v = await evaluate(s.eval); if (s.print) console.log(`  ${s.print}:`, typeof v === 'string' ? v : JSON.stringify(v)); }
  if (s.waitFor) {
    const t0 = Date.now();
    while (Date.now() - t0 < (s.timeout || 90000) && !(await evaluate(s.waitFor))) await sleep(500);
    console.log(`  waited ${((Date.now() - t0) / 1000).toFixed(1)} s for: ${s.waitFor.slice(0, 80)}`);
  }
  if (s.sleep) await sleep(s.sleep);
  if (s.shot) {
    const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    writeFileSync(join(outDir, s.shot), Buffer.from(r.result.data, 'base64'));
    console.log('  saved', s.shot);
  }
}
ws.close();
edge.kill();
process.exit(0);
