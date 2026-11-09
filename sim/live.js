// Live mode: a hands-free conversation with the agent (like Gemini Live or ChatGPT voice).
//
// Plain-language overview:
// - The microphone stays on. A tiny audio script (an AudioWorklet) turns it into 16 kHz, 16-bit
//   frames of exactly 1,024 bytes (32 ms each) and sends them to the backend over a WebSocket.
// - The backend relays them to Gnani Prisma streaming, which detects when you start talking
//   ("speech_start") and, after 0.7 s of silence, sends what you said ("transcript").
// - The agent's spoken lines are fetched from Gnani Timbre streaming as raw audio pieces and played
//   back to back as they arrive, one line after another (a queue).
// - Talking over the agent interrupts it: its voice drops when you start, and stops when your words
//   arrive. Words that match what the agent itself just said are ignored (its voice picked up by the
//   mic, if echo cancellation missed it).
// - Speakers or earphones: with speakers the browser's echo cancellation must stay on (or the mic hears
//   the agent), but it also damps your voice while the agent talks, so interrupting is weak. With
//   earphones there is no echo: it is switched off and interrupting works reliably (tested Nov 9).
// - The visitor's Gnani key (sim/keys.js) goes with each spoken line and, because browsers cannot
//   add headers to a WebSocket, as the first message of the speech-to-text connection.

import { keyHeaders, gnaniKey } from './keys.js';

const FRAME_SAMPLES = 512;      // 512 samples x 2 bytes = the 1,024-byte frame Prisma requires
const DUCK_VOLUME = 0.25;       // the agent's volume while you are talking over it

// Runs on the audio thread: average the microphone samples down to 16 kHz and post 512-sample frames.
// `sampleRate` is the microphone context's rate (usually 48000), a global inside a worklet.
const WORKLET = `
class Frames extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.pos = 0; this.sum = 0; this.n = 0;
    this.frame = new Int16Array(${FRAME_SAMPLES}); this.i = 0; this.peak = 0;
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let k = 0; k < ch.length; k++) {
      this.sum += ch[k]; this.n++; this.pos++;
      if (this.pos >= this.step) {
        this.pos -= this.step;
        const s = Math.max(-1, Math.min(1, this.sum / this.n));
        this.sum = 0; this.n = 0;
        this.frame[this.i++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        this.peak = Math.max(this.peak, Math.abs(s));
        if (this.i === ${FRAME_SAMPLES}) {
          this.port.postMessage({ frame: this.frame.buffer, level: this.peak }, [this.frame.buffer]);
          this.frame = new Int16Array(${FRAME_SAMPLES}); this.i = 0; this.peak = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('frames', Frames);`;

// ---------- echo guard ----------

// \p{M} keeps Hindi and Kannada vowel signs (matras) inside their words.
const words = (text) => (text || '').toLowerCase().normalize('NFC').match(/[\p{L}\p{M}\p{N}]+/gu) || [];

// True when most words of what the mic heard were just spoken by the agent itself.
export function looksLikeEcho(heard, spokenTexts) {
  const h = words(heard);
  if (h.length < 2) return false;
  const said = new Set(spokenTexts.flatMap(words));
  return h.filter((w) => said.has(w)).length / h.length >= 0.6;
}

// ---------- the agent's voice: streamed PCM, played as it arrives ----------

function createPlayer(api) {
  let ctx = null;
  let gain = null;
  let queue = [];          // [{text, resolve}]
  let current = null;      // {abort: AbortController, sources: Set, resolve}
  let recent = [];         // [{text, until}] what was said in the last few seconds (echo guard)
  let onChange = () => {};

  function ensureContext() {
    if (!ctx) {
      ctx = new AudioContext();
      gain = ctx.createGain();
      gain.connect(ctx.destination);
    }
    if (ctx.state === 'suspended') ctx.resume();
  }

  async function playOne(text) {
    const abort = new AbortController();
    const sources = new Set();
    current = { abort, sources };
    onChange();
    recent.push({ text, until: Infinity });
    let nextTime = 0;
    let carry = null; // an odd byte left over between network chunks
    try {
      const res = await fetch(`${api}/speak/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...keyHeaders() },
        body: JSON.stringify({ text }),
        signal: abort.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      const rate = Number(res.headers.get('X-Sample-Rate')) || 24000;
      const reader = res.body.getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        let bytes = value;
        if (carry) { bytes = new Uint8Array(carry.length + value.length); bytes.set(carry); bytes.set(value, carry.length); }
        const usable = bytes.length - (bytes.length % 2);
        carry = usable < bytes.length ? bytes.slice(usable) : null;
        if (!usable) continue;
        const pcm = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + usable));
        const buffer = ctx.createBuffer(1, pcm.length, rate);
        const ch = buffer.getChannelData(0);
        for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(gain);
        // Back to back; if the network fell behind, restart slightly in the future instead of in the past.
        nextTime = Math.max(nextTime, ctx.currentTime + 0.06);
        src.start(nextTime);
        nextTime += buffer.duration;
        sources.add(src);
        src.onended = () => sources.delete(src);
      }
      // Wait until the last scheduled piece has played.
      const left = nextTime - ctx.currentTime;
      if (left > 0 && !abort.signal.aborted) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, left * 1000 + 50);
          abort.signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); });
        });
      }
    } finally {
      // Keep the text for the echo guard a little longer: the mic may still deliver it.
      const entry = recent.find((r) => r.text === text && r.until === Infinity);
      if (entry) entry.until = performance.now() + 4000;
      current = null;
      onChange();
    }
  }

  async function pump() {
    if (current) return;
    while (queue.length) {
      const { text, resolve, reject } = queue.shift();
      try { await playOne(text); resolve(); } catch (err) { if (err.name === 'AbortError') resolve(); else reject(err); }
    }
  }

  return {
    // Speak a line after the ones already queued. Resolves when it has been played (or was interrupted).
    say(text) {
      ensureContext();
      gain.gain.value = 1;
      const done = new Promise((resolve, reject) => queue.push({ text, resolve, reject }));
      pump();
      return done;
    },
    // Stop talking now and forget the queued lines (the user interrupted).
    stop() {
      for (const q of queue) q.resolve();
      queue = [];
      if (current) {
        current.abort.abort();
        for (const s of current.sources) { try { s.stop(); } catch {} }
      }
    },
    duck(on) { if (gain) gain.gain.setTargetAtTime(on ? DUCK_VOLUME : 1, ctx.currentTime, 0.05); },
    isSpeaking: () => !!current || queue.length > 0,
    recentTexts() {
      const now = performance.now();
      recent = recent.filter((r) => r.until > now);
      return recent.map((r) => r.text);
    },
    unlock: ensureContext,          // call from a click so the browser allows sound
    set onChange(fn) { onChange = fn; },
  };
}

// ---------- the live session ----------

// handlers: onState(state, detail), onTranscript(text, language), onError(message), onLevel(0..1)
// states: "connecting", "listening", "hearing" (you are talking), "transcribing", "off"
export function createLive(api, handlers) {
  const player = createPlayer(api);
  let mic = null;           // {stream, ctx, node}
  let ws = null;
  let on = false;
  let muted = false;
  let state = 'off';
  let retries = 0;
  let duckTimer = null;
  let earphones = false;
  let fatal = false;        // the connection was refused: do not reconnect

  // Lower the agent's voice while you might be interrupting. A noise that never becomes a transcript
  // (a cough, a door) must not leave it quiet, so the volume comes back after 5 s at the latest.
  function duck(on) {
    clearTimeout(duckTimer);
    player.duck(on);
    if (on) duckTimer = setTimeout(() => player.duck(false), 5000);
  }

  const setState = (s, detail = '') => { state = s; handlers.onState?.(s, detail); };
  player.onChange = () => handlers.onState?.(state, '');

  async function openMic() {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: !earphones, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' })));
    const node = new AudioWorkletNode(ctx, 'frames');
    ctx.createMediaStreamSource(stream).connect(node);
    node.port.onmessage = (e) => {
      handlers.onLevel?.(muted ? 0 : e.data.level);
      if (ws?.readyState === WebSocket.OPEN) ws.send(muted ? new ArrayBuffer(1024) : e.data.frame);
    };
    mic = { stream, ctx, node };
  }

  function connect() {
    setState('connecting');
    ws = new WebSocket(`${(api || location.origin).replace(/^http/, 'ws')}/live/stt`);
    ws.binaryType = 'arraybuffer';
    // First message: the key (empty = the server's own key, when it allows that). Sent before any
    // audio frame, because frames are only sent once the socket is open.
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', gnani_key: gnaniKey() }));
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === 'connected') { retries = 0; setState('listening'); }
      else if (msg.type === 'speech_start') {
        if (player.isSpeaking()) duck(true);           // maybe an interruption: lower the agent's voice
        setState('hearing');
      } else if (msg.type === 'processing') setState('transcribing');
      else if (msg.type === 'transcript') {
        setState('listening');
        const text = (msg.text || '').trim();
        if (!text || looksLikeEcho(text, player.recentTexts())) { duck(false); return; }
        clearTimeout(duckTimer);
        player.stop();                                 // a real interruption, or simply the next turn
        handlers.onTranscript?.(text, msg.detected_language_code || '');
      } else if (msg.type === 'error') {
        // An error before "connected" (no key, a rejected key): reconnecting would fail the same way.
        if (state === 'connecting') fatal = true;
        handlers.onError?.(msg.message || 'speech-to-text error');
      }
    };
    ws.onclose = () => {
      ws = null;
      if (!on) return;
      if (fatal) { stop(); return; }
      // Gnani closes a session after 15 minutes or on an error: reconnect quietly, backing off a little.
      retries += 1;
      if (retries > 5) { handlers.onError?.('Lost the connection to speech-to-text. Live mode stopped.'); stop(); return; }
      setState('connecting', 'reconnecting...');
      setTimeout(() => { if (on) connect(); }, Math.min(500 * retries, 3000));
    };
  }

  async function start() {
    if (on) return;
    player.unlock();
    on = true;
    retries = 0;
    fatal = false;
    try {
      await openMic();
    } catch (err) {
      on = false;
      setState('off');
      throw new Error(`Microphone not available: ${err.message}`);
    }
    connect();
  }

  function closeMic() {
    if (!mic) return;
    mic.node.disconnect();
    mic.stream.getTracks().forEach((t) => t.stop());
    mic.ctx.close();
    mic = null;
  }

  function stop() {
    on = false;
    player.stop();
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    closeMic();
    setState('off');
  }

  // Earphones on: no echo cancellation, so interrupting works well. Restarts the microphone if live
  // (the speech-to-text connection stays open).
  async function setEarphones(value) {
    earphones = !!value;
    if (on && mic) {
      closeMic();
      try { await openMic(); } catch (err) { handlers.onError?.(`Microphone not available: ${err.message}`); }
    }
  }

  return {
    start,
    stop,
    say: (text) => (on && text ? player.say(text) : Promise.resolve()),
    stopSpeaking: () => player.stop(),
    setMuted(m) { muted = m; },
    setEarphones,
    isOn: () => on,
    isSpeaking: () => player.isSpeaking(),
    state: () => state,
  };
}
