// Microphone recording as a 16 kHz mono WAV file (Prisma accepts WAV, not the browser's default WebM).
//
// Plain-language overview:
// - getUserMedia asks for the microphone.
// - An AudioWorklet (a tiny audio script running on the audio thread) hands us the raw samples.
// - On stop, we shrink the samples to 16,000 per second (plenty for speech) and wrap them
//   in a WAV header, which is what the backend forwards to Prisma.

const TARGET_RATE = 16000;
const MAX_SECONDS = 29; // Prisma REST accepts up to 30 s

// The worklet code must come from a URL; a Blob URL avoids an extra file.
const WORKLET = `
class Grab extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('grab', Grab);`;

let session = null; // {stream, ctx, node, chunks, timer}

export const isRecording = () => session !== null;

export async function startRecording(onAutoStop) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' })));
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'grab');
  const chunks = [];
  node.port.onmessage = (e) => chunks.push(e.data);
  source.connect(node);
  const timer = setTimeout(() => onAutoStop?.(), MAX_SECONDS * 1000);
  session = { stream, ctx, node, chunks, timer };
}

// Stops recording and returns {wav: Blob, seconds}.
export async function stopRecording() {
  const { stream, ctx, node, chunks, timer } = session;
  session = null;
  clearTimeout(timer);
  node.disconnect();
  stream.getTracks().forEach((t) => t.stop());
  const rate = ctx.sampleRate;
  await ctx.close();

  const samples = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let offset = 0;
  for (const c of chunks) { samples.set(c, offset); offset += c.length; }
  const pcm = downsample(samples, rate, TARGET_RATE);
  return { wav: encodeWav(pcm, TARGET_RATE), seconds: pcm.length / TARGET_RATE };
}

// Average neighbouring samples down to the target rate (a simple low-pass + decimate).
function downsample(input, from, to) {
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

// 16-bit PCM WAV: a 44-byte header followed by the samples.
function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buffer);
  const text = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  text(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE');
  text(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  text(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

export { encodeWav as _encodeWav, downsample as _downsample }; // for tests
