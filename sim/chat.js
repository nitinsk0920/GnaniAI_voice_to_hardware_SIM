// The chat panel: user and agent bubbles, live progress of a build, and the result card
// (verified checks, fixes, the code explained line by line, try-next suggestions).
// Everything the model wrote is added with textContent, never as HTML, so a reply cannot inject markup.

let list;                  // the scrolling message list
let onLines = () => {};    // (range "12-14", codeText?) -> highlight lines in the editor
let onSuggest = () => {};  // (text, mode) -> send a suggestion as a new message
let onReplay = () => {};   // replay the last spoken reply

const STEP_LABELS = {
  understanding: 'Understanding', planning: 'Planning', preflight: 'Checking pins', compiling: 'Compiling',
  verifying: 'Measuring in simulator', fixing: 'Fixing', explaining: 'Explaining', give_up: 'Giving up', done: 'Done',
};

export function initChat({ listEl, onLineClick, onSuggestion, onReplaySpeech }) {
  list = listEl;
  onLines = onLineClick;
  onSuggest = onSuggestion;
  onReplay = onReplaySpeech;
}

// ---------- small DOM helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scrollDown() {
  list.scrollTop = list.scrollHeight;
}

function chip(text, mode) {
  const b = el('button', 'chip', text);
  b.type = 'button';
  b.addEventListener('click', () => onSuggest(text, mode));
  return b;
}

// "line 12" / "lines 12-14" / "Line 12 to 14" in a reply become buttons that highlight the code.
const LINE_REF = /\b(lines?)\s+(\d+)(?:\s*(?:-|–|to)\s*(\d+))?/gi;

function lineButton(label, range) {
  const b = el('button', 'lineref', label);
  b.type = 'button';
  b.title = 'Show in the code editor';
  b.addEventListener('click', () => onLines(range));
  return b;
}

// Inline formatting: `code`, **bold**, and line references.
function inline(text) {
  const out = document.createDocumentFragment();
  for (const part of text.split(/(`[^`]+`|\*\*[^*]+\*\*)/)) {
    if (!part) continue;
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) out.append(el('code', '', part.slice(1, -1)));
    else if (part.startsWith('**') && part.endsWith('**') && part.length > 4) out.append(el('strong', '', part.slice(2, -2)));
    else {
      let last = 0;
      for (const m of part.matchAll(LINE_REF)) {
        out.append(part.slice(last, m.index));
        out.append(lineButton(m[0], m[3] ? `${m[2]}-${m[3]}` : m[2]));
        last = m.index + m[0].length;
      }
      out.append(part.slice(last));
    }
  }
  return out;
}

// Block formatting: paragraphs, "- " / "1. " lists, and ``` code blocks.
export function richText(text) {
  const box = el('div', 'rich');
  const lines = String(text || '').replace(/\r/g, '').split('\n');
  let listEl = null;
  let para = [];
  const flush = () => {  // the lines collected so far become one paragraph
    if (para.length) {
      const p = el('p');
      p.append(inline(para.join(' ')));
      box.append(p);
    }
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith('```')) {           // code block until the closing fence
      flush(); listEl = null;
      const code = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith('```'); i++) code.push(lines[i]);
      box.append(el('pre', 'snippet', code.join('\n')));
      continue;
    }
    const bullet = line.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/);
    if (bullet) {
      flush();
      const kind = bullet[1] ? 'ol' : 'ul';
      if (!listEl || listEl.tagName.toLowerCase() !== kind) { listEl = el(kind); box.append(listEl); }
      const li = el('li'); li.append(inline(bullet[2])); listEl.append(li);
      continue;
    }
    listEl = null;
    if (!line.trim()) flush(); else para.push(line.trim());
  }
  flush();
  return box;
}

// ---------- messages ----------

export function clearChat() {
  list.replaceChildren();
}

export function showWelcome() {
  const msg = el('div', 'msg agent welcome');
  msg.append(richText("Hi! Tell me what your circuit should do, and I'll write the code, test it in the simulator and explain it line by line. "
    + 'You can also ask me anything about the code, the circuit or Arduino.'));
  const chips = el('div', 'chips');
  chips.append(chip('make the LED blink fast', 'build'), chip('add a button, and light the LED while I hold it', 'build'),
    chip('what can you simulate?', 'auto'));
  msg.append(chips);
  list.append(msg);
}

export function addUserMessage(text, { via = 'text', language = '' } = {}) {
  list.querySelector('.welcome')?.remove();
  const msg = el('div', 'msg user');
  if (via === 'voice') msg.append(el('span', 'via', `🎤 heard${language ? ` (${language})` : ''}`));
  msg.append(el('div', 'text', text));
  list.append(msg);
  scrollDown();
}

function spokenLine(text) {
  const p = el('p', 'spoken', `🔊 ${text}`);
  p.title = 'Click to hear it again';
  p.addEventListener('click', onReplay);
  return p;
}

export function addChatReply({ text, lines, spoken }) {
  const msg = el('div', 'msg agent');
  msg.append(richText(text));
  if (lines && !msg.querySelector('.lineref')) {  // only when the reply has no line links of its own
    const row = el('div', 'msg-foot');
    row.append(lineButton(`Show line${lines.includes('-') ? 's' : ''} ${lines} in the code`, lines));
    msg.append(row);
  }
  if (spoken) msg.append(spokenLine(spoken));
  list.append(msg);
  scrollDown();
  return msg;
}

export function addErrorMessage(text) {
  const msg = el('div', 'msg agent error');
  msg.append(el('strong', '', 'Something went wrong. '), el('span', '', text));
  list.append(msg);
  scrollDown();
}

// ---------- a request in progress: steps appear live, then turn into the reply ----------

function stepIsBad(s) {
  const allPassed = /^(\d+)\/\1 checks/.test(s.note || ''); // "2/2 checks passed ..."
  return s.step === 'give_up' || /failed/.test(s.note || '')
    || (s.step === 'verifying' && !allPassed)
    || (s.step === 'compiling' && !/: ok$/.test(s.note || ''))
    || (s.step === 'preflight' && !(s.note || '').startsWith('ok'));
}

function stepItem(s) {
  const li = el('li', stepIsBad(s) ? 'bad' : s.step === 'fixing' ? 'fix' : s.step === 'verifying' ? 'good' : '');
  li.append(el('span', 'step-name', STEP_LABELS[s.step] || s.step));
  if (s.note) li.append(el('span', 'step-note', `: ${s.note}`));
  li.append(el('span', 'step-time', ` ${(s.ms / 1000).toFixed(1)} s`));
  return li;
}

export function startPending() {
  const msg = el('div', 'msg agent pending');
  const head = el('div', 'working');
  head.append(el('span', 'dots'), el('span', '', 'Thinking...'));
  const steps = el('ol', 'steps live');
  msg.append(head, steps);
  list.append(msg);
  scrollDown();
  return {
    addStep(s) {
      if (s.step === 'done') return;
      head.lastChild.textContent = `${STEP_LABELS[s.step] || s.step}...`;
      if (s.step !== 'understanding') steps.append(stepItem(s));
      scrollDown();
    },
    addSay(text) {  // what the agent says while it works ("Sure, one moment", "fixing it")
      steps.before(el('p', 'say', `🗣 ${text}`));
      scrollDown();
    },
    remove() { msg.remove(); },
  };
}

// ---------- the result card of a build ----------

function section(title, child, className = '') {
  const box = el('div', `section ${className}`);
  box.append(el('h4', '', title), child);
  return box;
}

function lineTable(rows) {
  const table = el('div', 'line-table');
  for (const r of rows) {
    const row = el('button', 'line-row');
    row.type = 'button';
    row.title = 'Show this line in the code editor';
    row.append(el('span', 'ln', String(r.line)));
    const body = el('span', 'line-body');
    body.append(el('code', '', r.code));
    if (r.note) body.append(el('span', 'note', r.note));
    row.append(body);
    row.addEventListener('click', () => onLines(String(r.line), r.code));
    table.append(row);
  }
  return table;
}

export function addBuildCard(card, { latest = true } = {}) {
  const ex = card.explanation || {};
  const passed = card.status === 'passed';
  const msg = el('div', `msg agent build ${passed ? 'passed' : 'failed'}`);

  const secs = ((card.elapsed_ms || 0) / 1000).toFixed(1);
  const fixes = card.attempts ? ` · fixed itself ${card.attempts}x` : '';
  msg.append(el('div', `badge ${passed ? 'good' : 'bad'}`,
    passed ? `✓ Built and verified in ${secs} s${fixes}` : `✗ Could not get it working (${secs} s)`));

  msg.append(richText(ex.summary || card.behavior_summary || card.problem || ''));
  const changes = card.circuit_changes || [];
  if (changes.length) msg.append(el('div', 'notice', `I changed your wiring: ${changes.join('; ')}.`));
  if (card.spoken_summary) msg.append(spokenLine(card.spoken_summary));

  const verified = ex.verified || [];
  if (verified.length) {
    const ul = el('ul', 'verified');
    for (const v of verified) ul.append(el('li', v.ok ? 'ok' : 'bad', `${v.ok ? '✓' : '✗'} ${v.detail}`));
    msg.append(section('Measured in the simulator', ul));
  }
  const fixesMade = ex.fixes_made || [];
  if (fixesMade.length) {
    const ul = el('ul', 'fixes');
    for (const f of fixesMade) ul.append(el('li', '', `Attempt ${f.attempt} (${f.stage}): ${f.change}`));
    msg.append(section('Fixed along the way', ul));
  }
  if (!passed && !verified.length && card.problem && ex.summary !== card.problem) {
    msg.append(el('pre', 'problem', card.problem));
  }

  const rows = ex.lines || [];
  if (rows.length) {
    const details = el('details', 'explain-lines');
    details.open = latest;
    details.append(el('summary', '', `The code, line by line (${rows.length} lines) · click a line to see it in the editor`), lineTable(rows));
    msg.append(details);
  }

  const steps = (card.steps || []).filter((s) => s.step !== 'understanding' && s.step !== 'done');
  if (steps.length) {
    const details = el('details', 'how');
    const ol = el('ol', 'steps');
    steps.forEach((s) => ol.append(stepItem(s)));
    details.append(el('summary', '', `How I got there (${steps.length} steps)`), ol);
    msg.append(details);
  }

  if ((ex.try_next || []).length) {
    const chips = el('div', 'chips');
    chips.append(el('span', 'chips-label', 'Try next:'), ...ex.try_next.map((t) => chip(t, 'build')));
    msg.append(chips);
  }

  if (latest) {  // keep the chat short: older explanations fold up
    for (const old of list.querySelectorAll('details.explain-lines[open]')) old.open = false;
  }
  list.append(msg);
  // Show the top of the card (result, summary, measurements); the line-by-line part is below it.
  list.scrollTop += msg.getBoundingClientRect().top - list.getBoundingClientRect().top - 8;
  return msg;
}

// Redraw a stored conversation (after a page reload).
export function renderHistory(messages) {
  clearChat();
  const lastBuild = messages.map((m) => m.kind).lastIndexOf('build');
  messages.forEach((m, i) => {
    if (m.role === 'user') addUserMessage(m.text, { via: m.via });
    else if (m.kind === 'build' && m.card) addBuildCard(m.card, { latest: i === lastBuild });
    else addChatReply({ text: m.text, lines: m.lines, spoken: m.spoken });
  });
}
