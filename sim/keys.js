// Bring your own key (BYOK): the visitor's API keys, kept only in this browser.
// They are sent as headers with the visitor's own requests (and, for Live mode, in the first
// WebSocket message). The backend uses them for that request only and never stores or logs them.
// "Remember on this device" keeps them in localStorage; otherwise sessionStorage (gone when the tab closes).

const STORE = 'gnani_keys';
const NAMES = ['groq', 'gemini', 'gnani'];
const HEADERS = { groq: 'X-Groq-Key', gemini: 'X-Gemini-Key', gnani: 'X-Gnani-Key' };

let keys = load();

function clean(obj) {
  const out = {};
  for (const name of NAMES) {
    const v = String(obj?.[name] || '').trim();
    if (v) out[name] = v;
  }
  return out;
}

function load() {
  try {
    return clean(JSON.parse(localStorage.getItem(STORE) || sessionStorage.getItem(STORE) || '{}'));
  } catch {
    return {};
  }
}

export const getKeys = () => ({ ...keys });
export const gnaniKey = () => keys.gnani || '';
export const hasLlmKey = () => !!(keys.groq || keys.gemini);

export function isRemembered() {
  try { return !!localStorage.getItem(STORE); } catch { return false; }
}

export function saveKeys(next, remember) {
  keys = clean(next);
  try {
    localStorage.removeItem(STORE);
    sessionStorage.removeItem(STORE);
    if (Object.keys(keys).length) (remember ? localStorage : sessionStorage).setItem(STORE, JSON.stringify(keys));
  } catch {}
}

export const forgetKeys = () => saveKeys({}, false);

// Headers for a fetch to our backend: only the keys the visitor entered.
export function keyHeaders() {
  const h = {};
  for (const name of NAMES) if (keys[name]) h[HEADERS[name]] = keys[name];
  return h;
}
