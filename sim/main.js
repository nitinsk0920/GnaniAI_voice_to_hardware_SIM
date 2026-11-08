// Page logic: chat with the agent (POST /run, streamed), compile by hand (POST /compile),
// run the hex live, and keep the session across page reloads (GET /session/<id>).
import '@wokwi/elements'; // registers <wokwi-led>, <wokwi-servo>, <wokwi-arduino-uno>, ...
import { startSimulation, stopSimulation } from './simulator.js';
import { resetSignals, record, prune, drawGraph } from './signals.js';
import { startRecording, stopRecording, isRecording } from './voice.js';
import {
  initCircuit, getCircuit, setCircuit, setController, simSetup, graphRows,
  updateParts, resetParts, replaceDefineBlock, unlockAudio, setSound, BOARDS, setBoard, getBoardId,
} from './circuit.js';
import {
  initChat, clearChat, showWelcome, addUserMessage, addChatReply, addBuildCard, addErrorMessage,
  startPending, renderHistory,
} from './chat.js';

const API = 'http://localhost:8000';
const DEFAULT_CIRCUIT = { components: [{ type: 'led', id: 'LED1', pin: 13 }] };
const DEFAULT_BODY = `void setup() {
  pinMode(LED1_PIN, OUTPUT);
}

void loop() {
  digitalWrite(LED1_PIN, HIGH);
  delay(500);
  digitalWrite(LED1_PIN, LOW);
  delay(500);
}
`;

const $ = (id) => document.getElementById(id);
const codeBox = $('code');
const gutter = $('gutter');
const statusLine = $('status');
const errorBox = $('error');
const agentStatus = $('agentStatus');
const graph = $('graph');

let sessionId = loadSessionId();
let busy = false;

// ---------- session id (kept in the browser so follow-ups continue the same session) ----------

function loadSessionId() {
  try {
    const saved = localStorage.getItem('gnani_session');
    if (saved) return saved;
  } catch {}
  return newSessionId();
}

function newSessionId() {
  const id = crypto.randomUUID();
  try { localStorage.setItem('gnani_session', id); } catch {}
  return id;
}

// ---------- code editor with line numbers ----------
// A <textarea> has no line numbers, so a <pre> next to it lists 1, 2, 3 ... and scrolls with it.

let gutterLines = 0;
function updateGutter() {
  const count = codeBox.value.split('\n').length;
  if (count !== gutterLines) {
    gutterLines = count;
    gutter.replaceChildren(...Array.from({ length: count }, (_, i) => {
      const n = document.createElement('span');
      n.textContent = String(i + 1);
      return n;
    }));
  }
  gutter.scrollTop = codeBox.scrollTop;
}

function markGutter(first, last) {
  [...gutter.children].forEach((n, i) => n.classList.toggle('hl', i + 1 >= first && i + 1 <= last));
}

function setCode(text) {
  codeBox.value = text;
  updateGutter();
  markGutter(0, -1);
}

// Show lines in the editor ("12" or "12-14"): mark them in the line-number column and scroll there.
// focus: also select them (when the user clicked a line); a chat reply only marks them, so the
// cursor stays in the chat box. If the code has shifted since the explanation was written
// (e.g. a part was added, so there are more #define lines), the line is found by its text.
function highlightLines(range, codeText, { focus = true } = {}) {
  const m = String(range).match(/(\d+)(?:\s*-\s*(\d+))?/);
  if (!m) return;
  const lines = codeBox.value.split('\n');
  let first = Number(m[1]);
  let last = Number(m[2] || m[1]);
  if (codeText && lines[first - 1]?.trim() !== codeText) {
    const found = lines.findIndex((l) => l.trim() === codeText);
    if (found >= 0) { last = found + 1 + (last - first); first = found + 1; }
  }
  first = Math.max(1, Math.min(first, lines.length));
  last = Math.max(first, Math.min(last, lines.length));
  markGutter(first, last);
  const lineHeight = parseFloat(getComputedStyle(codeBox).lineHeight) || 20;
  codeBox.scrollTop = Math.max(0, (first - 4) * lineHeight);
  updateGutter();
  if (!focus) return;
  const start = lines.slice(0, first - 1).join('\n').length + (first > 1 ? 1 : 0);
  const end = lines.slice(0, last).join('\n').length;
  codeBox.focus({ preventScroll: true });
  codeBox.setSelectionRange(start, end);
  codeBox.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---------- simulation ----------

function stop() {
  stopSimulation();
  resetParts();
}

function run(hex) {
  stop();
  resetSignals();
  const setup = simSetup();
  const controller = startSimulation(hex, {
    ...setup,
    onPin: record,
    onTime: (ms) => {
      prune(ms);
      updateParts(ms);
      drawGraph(graph, graphRows(), ms);
      $('simTime').textContent = `· ${(ms / 1000).toFixed(1)} s simulated`;
    },
  });
  setController(controller);
}

function showError(text) {
  errorBox.textContent = text;
  errorBox.hidden = !text;
}

function showWiringNotice(text) {
  $('wiringNotice').hidden = !text;
  $('wiringNotice').textContent = text || '';
}

function onCircuitChange(circuit) {
  // Keep the #define lines in the editor in sync with the circuit panel.
  stop();
  setCode(replaceDefineBlock(codeBox.value, circuit));
  drawGraph(graph, graphRows(), 0);
  statusLine.textContent = 'Circuit changed. Press Compile & Run, or ask the agent.';
}

// ---------- manual compile ----------

async function compileAndRun() {
  unlockAudio();
  $('runBtn').disabled = true;
  showError('');
  statusLine.textContent = 'Compiling...';
  stop();
  try {
    const res = await fetch(`${API}/compile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: codeBox.value, board: getBoardId() }),
    });
    const data = await res.json();
    const secs = (data.compile_ms / 1000).toFixed(1);
    if (!data.ok) {
      statusLine.textContent = `Compile failed (${secs} s).`;
      showError(data.error);
      return;
    }
    statusLine.textContent = `Compiled in ${secs} s. Running.`;
    run(data.hex);
  } catch (err) {
    statusLine.textContent = 'Could not reach the backend. Is uvicorn running on port 8000?';
    showError(String(err));
  } finally {
    $('runBtn').disabled = false;
  }
}

// ---------- chat with the agent (Server-Sent Events over fetch, because EventSource cannot POST) ----------

function setBusy(on) {
  busy = on;
  $('askBtn').disabled = on;
  $('micBtn').disabled = on;
}

// mode: "auto" (the agent decides: answer or build), "build" (a suggestion chip), "chat".
async function askAgent(text, { mode = 'auto', via = 'text', language = '' } = {}) {
  text = (text || '').trim();
  if (!text || busy) return;
  unlockAudio();
  setBusy(true);
  addUserMessage(text, { via, language });
  const pending = startPending();
  agentStatus.className = 'status';
  agentStatus.textContent = 'Agent working...';
  showError('');

  try {
    const res = await fetch(`${API}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId, text, circuit: getCircuit(), board: getBoardId(),
        debug_break: $('debugBreak').value, mode, via,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (!chunk.startsWith('data: ')) continue;
        const event = JSON.parse(chunk.slice(6));
        if (event.type === 'step') pending.addStep(event);
        else if (event.type === 'result') { pending.remove(); showResult(event); }
        else if (event.type === 'error') throw new Error(event.message);
      }
    }
  } catch (err) {
    pending.remove();
    agentStatus.className = 'status bad';
    agentStatus.textContent = 'Agent request failed.';
    addErrorMessage(err.message === 'Failed to fetch'
      ? 'Could not reach the backend. Is uvicorn running on port 8000?' : String(err.message || err));
  } finally {
    setBusy(false);
    $('request').focus();
  }
}

function showResult(data) {
  const secs = (data.elapsed_ms / 1000).toFixed(1);
  if (data.kind === 'chat') {
    addChatReply({ text: data.reply, lines: data.lines, spoken: data.spoken_summary });
    agentStatus.className = 'status';
    agentStatus.textContent = `Answered in ${secs} s.`;
    if (data.lines) highlightLines(data.lines, undefined, { focus: false });
    speakReply(data.spoken_summary);
    return;
  }

  setCircuit(data.circuit, { highlight: true });
  const changes = data.circuit_changes || [];
  showWiringNotice(changes.length ? `The agent changed your wiring: ${changes.join('; ')}.` : '');
  if (data.full_code) setCode(data.full_code);
  addBuildCard(data);

  const fixes = data.attempts ? `, fixed itself ${data.attempts}x` : '';
  if (data.status === 'passed') {
    agentStatus.className = 'status good';
    agentStatus.textContent = `Verified in ${secs} s${fixes}. Running it now.`;
    statusLine.textContent = "Running the agent's code.";
    run(data.hex);
  } else {
    agentStatus.className = 'status bad';
    agentStatus.textContent = `The agent could not get it working (${secs} s).`;
    stop();
  }
  speakReply(data.spoken_summary);
}

// ---------- voice: Prisma (speech-to-text) in, Timbre (text-to-speech) out ----------

const micBtn = $('micBtn');
let replyAudio = null;

function showHeard(text) {
  $('heard').hidden = !text;
  $('heard').textContent = text || '';
}

async function toggleMic() {
  unlockAudio();
  if (isRecording()) return finishRecording();
  try {
    await startRecording(finishRecording); // auto-stops before Prisma's 30 s limit
    micBtn.classList.add('recording');
    micBtn.textContent = '■ Stop';
    showHeard('Listening... click Stop when you are done.');
  } catch (err) {
    showHeard(`Microphone not available: ${err.message}`);
  }
}

async function finishRecording() {
  if (!isRecording()) return;
  micBtn.classList.remove('recording');
  micBtn.classList.add('busy');
  micBtn.textContent = '…';
  let transcript = null;
  let language = '';
  try {
    const { wav, seconds } = await stopRecording();
    if (seconds < 0.4) return showHeard('That was too short. Click Speak, say your request, then click Stop.');
    showHeard('Transcribing with Gnani Prisma...');
    const res = await fetch(`${API}/transcribe`, { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (!data.transcript) return showHeard("I didn't catch anything. Please try again.");
    showHeard('');
    transcript = data.transcript;
    language = data.language || '';
  } catch (err) {
    showHeard(`Speech-to-text failed: ${err.message}`);
  } finally {
    micBtn.classList.remove('busy');
    micBtn.textContent = '🎤 Speak';
  }
  if (transcript) askAgent(transcript, { via: 'voice', language });
}

// Read the agent's short reply aloud (never the code). One Timbre call per reply.
async function speakReply(text) {
  if (!text || !$('speakToggle').checked) return;
  try {
    const res = await fetch(`${API}/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    replyAudio?.pause();
    replyAudio = new Audio(URL.createObjectURL(await res.blob()));
    await replyAudio.play();
  } catch (err) {
    const last = [...document.querySelectorAll('#messages .spoken')].pop();
    last?.append(Object.assign(document.createElement('span'), {
      className: 'caption', textContent: `  (voice reply unavailable: ${err.message})`,
    }));
  }
}

function replaySpeech() {
  if (replyAudio) { replyAudio.currentTime = 0; replyAudio.play(); }
}

// ---------- session ----------

function resetBench() {
  stop();
  setCircuit(DEFAULT_CIRCUIT);
  setCode(replaceDefineBlock(DEFAULT_BODY, DEFAULT_CIRCUIT));
  showWiringNotice('');
  showError('');
  drawGraph(graph, graphRows(), 0);
}

function newSession() {
  sessionId = newSessionId();
  resetBench();
  clearChat();
  showWelcome();
  agentStatus.className = 'status';
  agentStatus.textContent = 'New session. Ready.';
  statusLine.textContent = 'Ready.';
}

// After a page reload: bring back the conversation, board, circuit and code of this session.
async function restoreSession() {
  try {
    const res = await fetch(`${API}/session/${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.messages?.length) return showWelcome();
    if (data.board && data.board !== getBoardId()) chooseBoard(data.board);
    if (data.circuit) setCircuit(data.circuit);
    if (data.full_code) setCode(data.full_code);
    else setCode(replaceDefineBlock(codeBox.value, getCircuit()));
    showWiringNotice('');
    renderHistory(data.messages);
    drawGraph(graph, graphRows(), 0);
    agentStatus.textContent = 'Session restored.';
    statusLine.textContent = data.hex ? 'Session restored. Press Compile & Run to run the last code.' : 'Session restored.';
  } catch {
    showWelcome(); // backend not running yet: start fresh; the session continues once it is
  }
}

// ---------- wire up ----------

initCircuit({
  partsEl: $('parts'), addTypeEl: $('addType'), addBtn: $('addBtn'), mcuEl: $('mcu'),
  stageEl: $('stage'), boardEl: $('boardWrap'), wiresEl: $('wires'), onCircuitChange,
});
initChat({
  listEl: $('messages'),
  onLineClick: highlightLines,
  onSuggestion: (text, mode) => askAgent(text, { mode }),
  onReplaySpeech: replaySpeech,
});
setCircuit(DEFAULT_CIRCUIT);
setCode(replaceDefineBlock(DEFAULT_BODY, DEFAULT_CIRCUIT));

// Board choice (remembered in this browser). Changing it stops the run and keeps the parts,
// moving any part whose pin the new board does not have.
const boardSelect = $('boardSelect');
for (const b of Object.values(BOARDS)) boardSelect.append(new Option(b.name, b.id));
function chooseBoard(id) {
  const changes = setBoard(id);
  boardSelect.value = getBoardId();
  try { localStorage.setItem('gnani_board', getBoardId()); } catch {}
  showWiringNotice(changes.length ? `Switched board: ${changes.join('; ')}.` : '');
}
let savedBoard = 'uno';
try { savedBoard = localStorage.getItem('gnani_board') || 'uno'; } catch {}
if (savedBoard !== 'uno') chooseBoard(savedBoard);
boardSelect.value = getBoardId();
boardSelect.addEventListener('change', () => chooseBoard(boardSelect.value));
drawGraph(graph, graphRows(), 0);

$('askForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = $('request').value;
  $('request').value = '';
  askAgent(text);
});
codeBox.addEventListener('input', updateGutter);
codeBox.addEventListener('scroll', () => { gutter.scrollTop = codeBox.scrollTop; });
$('runBtn').addEventListener('click', compileAndRun);
$('stopBtn').addEventListener('click', () => { stop(); statusLine.textContent = 'Stopped.'; });
$('newSessionBtn').addEventListener('click', newSession);
$('soundToggle').addEventListener('change', (e) => setSound(e.target.checked));
micBtn.addEventListener('click', toggleMic);

restoreSession();
