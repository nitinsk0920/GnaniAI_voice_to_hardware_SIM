// Page logic: ask the agent (POST /run, streamed) or compile by hand (POST /compile),
// then run the hex live and show the explanation.
import '@wokwi/elements'; // registers <wokwi-led>, <wokwi-servo>, <wokwi-arduino-uno>, ...
import { startSimulation, stopSimulation } from './simulator.js';
import { resetSignals, record, prune, drawGraph } from './signals.js';
import { startRecording, stopRecording, isRecording } from './voice.js';
import {
  initCircuit, getCircuit, setCircuit, setController, simSetup, graphRows,
  updateParts, resetParts, replaceDefineBlock, unlockAudio, setSound, BOARDS, setBoard, getBoardId,
} from './circuit.js';

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
const statusLine = $('status');
const errorBox = $('error');
const agentStatus = $('agentStatus');
const stepsList = $('steps');
const graph = $('graph');

let sessionId = loadSessionId();

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

function onCircuitChange(circuit) {
  // Keep the #define lines in the editor in sync with the circuit panel.
  stop();
  codeBox.value = replaceDefineBlock(codeBox.value, circuit);
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

// ---------- agent (Server-Sent Events over fetch, because EventSource cannot POST) ----------

function addStep(s) {
  const li = document.createElement('li');
  const allPassed = /^(\d+)\/\1 checks/.test(s.note); // "2/2 checks passed ..."
  const bad = s.step === 'give_up' || /failed/.test(s.note)
    || (s.step === 'verifying' && !allPassed)
    || (s.step === 'compiling' && !/: ok$/.test(s.note))
    || (s.step === 'preflight' && s.note !== 'ok');
  li.className = bad ? 'bad' : s.step === 'fixing' ? 'fix' : s.step === 'verifying' ? 'good' : '';
  const label = { planning: 'Planning', preflight: 'Checking pins', compiling: 'Compiling', verifying: 'Measuring in simulator',
    fixing: 'Fixing', explaining: 'Explaining', give_up: 'Giving up', done: 'Done' }[s.step] || s.step;
  li.textContent = `${label}${s.note ? ': ' + s.note : ''}  (${(s.ms / 1000).toFixed(1)} s)`;
  stepsList.append(li);
}

async function askAgent(event) {
  event?.preventDefault();
  const text = $('request').value.trim();
  if (!text) return;
  unlockAudio();

  $('askBtn').disabled = true;
  agentStatus.className = 'status';
  agentStatus.textContent = 'Agent working...';
  $('wiringNotice').hidden = true;
  stepsList.innerHTML = '';
  showError('');

  try {
    const res = await fetch(`${API}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, text, circuit: getCircuit(), board: getBoardId(), debug_break: $('debugBreak').value }),
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
        if (event.type === 'step') addStep(event);
        else if (event.type === 'result') showResult(event);
        else if (event.type === 'error') throw new Error(event.message);
      }
    }
  } catch (err) {
    agentStatus.className = 'status bad';
    agentStatus.textContent = 'Agent request failed.';
    showError(String(err));
  } finally {
    $('askBtn').disabled = false;
  }
}

function showResult(data) {
  setCircuit(data.circuit, { highlight: true });
  const changes = data.circuit_changes || [];
  $('wiringNotice').hidden = !changes.length;
  $('wiringNotice').textContent = changes.length ? `The agent changed your wiring: ${changes.join('; ')}.` : '';
  if (data.full_code) codeBox.value = data.full_code;
  renderExplanation(data);

  const secs = (data.elapsed_ms / 1000).toFixed(1);
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
    // Measured failures are already listed in the explanation panel; show raw text only otherwise.
    if (!(data.explanation?.verified || []).length) showError(data.problem || 'unknown problem');
  }
  speakReply(data.spoken_summary);
}

// ---------- explanation panel (built with textContent: LLM text is never parsed as HTML) ----------

function item(tag, text, className) {
  const el = document.createElement(tag);
  el.textContent = text;
  if (className) el.className = className;
  return el;
}

function renderExplanation(data) {
  const ex = data.explanation || {};
  $('explanation').hidden = false;
  $('exSummary').textContent = ex.summary || data.behavior_summary || '';
  $('exSpoken').textContent = data.spoken_summary ? `🔊 ${data.spoken_summary}` : '';

  $('exVerified').replaceChildren(...(ex.verified || []).map((v) =>
    item('li', `${v.ok ? '✓' : '✗'} ${v.detail}`, v.ok ? 'ok' : 'bad')));

  const fixes = ex.fixes_made || [];
  $('exFixesBox').hidden = !fixes.length;
  $('exFixes').replaceChildren(...fixes.map((f) => item('li', `Attempt ${f.attempt} (${f.stage}): ${f.change}`)));

  // Skip empty steps; show "Lines ..." only when the model gave line numbers.
  const steps = (ex.walkthrough || []).filter((w) => w && (w.what || w.why));
  $('exWalk').replaceChildren(...steps.map((w) => {
    const li = document.createElement('li');
    if (w.lines) li.append(item('span', `Lines ${w.lines}: `, 'lines'));
    li.append(item('span', w.what || ''), item('div', w.why || '', 'why'));
    if (w.lines) li.addEventListener('click', () => highlightLines(String(w.lines)));
    return li;
  }));

  $('exTry').replaceChildren(...(ex.try_next || []).map((t) => {
    const b = item('button', t, 'chip');
    b.type = 'button';
    b.addEventListener('click', () => { $('request').value = t; askAgent(); });
    return b;
  }));
}

function highlightLines(range) {
  const m = range.match(/(\d+)(?:\s*-\s*(\d+))?/);
  if (!m) return;
  const lines = codeBox.value.split('\n');
  const first = Math.max(1, Number(m[1]));
  const last = Math.min(lines.length, Number(m[2] || m[1]));
  const start = lines.slice(0, first - 1).join('\n').length + (first > 1 ? 1 : 0);
  const end = lines.slice(0, last).join('\n').length;
  codeBox.focus();
  codeBox.setSelectionRange(start, end);
  const lineHeight = parseFloat(getComputedStyle(codeBox).lineHeight) || 20;
  codeBox.scrollTop = (first - 3) * lineHeight;
}

// ---------- voice: Prisma (speech-to-text) in, Timbre (text-to-speech) out ----------

const micBtn = $('micBtn');
let replyAudio = null;

function showHeard(text, language) {
  const el = $('heard');
  el.hidden = false;
  if (language === undefined) el.textContent = text;
  else el.replaceChildren(item('span', 'Heard: '), item('strong', text), item('span', language ? `  (${language})` : ''));
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
  try {
    const { wav, seconds } = await stopRecording();
    if (seconds < 0.4) return showHeard('That was too short. Click Speak, say your request, then click Stop.');
    showHeard('Transcribing with Gnani Prisma...');
    const res = await fetch(`${API}/transcribe`, { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (!data.transcript) return showHeard("I didn't catch anything. Please try again.");
    showHeard(data.transcript, data.language);
    $('request').value = data.transcript;
    askAgent();
  } catch (err) {
    showHeard(`Speech-to-text failed: ${err.message}`);
  } finally {
    micBtn.classList.remove('busy');
    micBtn.textContent = '🎤 Speak';
  }
}

// Read the agent's short summary aloud (never the code). One Timbre call per result.
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
    $('exSpoken').append(item('span', `  (voice reply unavailable: ${err.message})`, 'caption'));
  }
}

// ---------- session ----------

function newSession() {
  stop();
  sessionId = newSessionId();
  setCircuit(DEFAULT_CIRCUIT);
  codeBox.value = replaceDefineBlock(DEFAULT_BODY, DEFAULT_CIRCUIT);
  stepsList.innerHTML = '';
  $('explanation').hidden = true;
  showError('');
  agentStatus.className = 'status';
  agentStatus.textContent = 'New session. Ready.';
  drawGraph(graph, graphRows(), 0);
}

// ---------- wire up ----------

initCircuit({
  partsEl: $('parts'), addTypeEl: $('addType'), addBtn: $('addBtn'), mcuEl: $('mcu'),
  stageEl: $('stage'), boardEl: $('boardWrap'), wiresEl: $('wires'), onCircuitChange,
});
setCircuit(DEFAULT_CIRCUIT);
codeBox.value = replaceDefineBlock(DEFAULT_BODY, DEFAULT_CIRCUIT);

// Board choice (remembered in this browser). Changing it stops the run and keeps the parts,
// moving any part whose pin the new board does not have.
const boardSelect = $('boardSelect');
for (const b of Object.values(BOARDS)) boardSelect.append(new Option(b.name, b.id));
function chooseBoard(id) {
  const changes = setBoard(id);
  boardSelect.value = getBoardId();
  try { localStorage.setItem('gnani_board', getBoardId()); } catch {}
  $('wiringNotice').hidden = !changes.length;
  $('wiringNotice').textContent = changes.length ? `Switched board: ${changes.join('; ')}.` : '';
}
let savedBoard = 'uno';
try { savedBoard = localStorage.getItem('gnani_board') || 'uno'; } catch {}
if (savedBoard !== 'uno') chooseBoard(savedBoard);
boardSelect.value = getBoardId();
boardSelect.addEventListener('change', () => chooseBoard(boardSelect.value));
drawGraph(graph, graphRows(), 0);

$('askForm').addEventListener('submit', askAgent);
$('runBtn').addEventListener('click', compileAndRun);
$('stopBtn').addEventListener('click', () => { stop(); statusLine.textContent = 'Stopped.'; });
$('newSessionBtn').addEventListener('click', newSession);
$('soundToggle').addEventListener('change', (e) => setSound(e.target.checked));
micBtn.addEventListener('click', toggleMic);
$('exSpoken').title = 'Click to hear it again';
$('exSpoken').addEventListener('click', () => { if (replyAudio) { replyAudio.currentTime = 0; replyAudio.play(); } });
