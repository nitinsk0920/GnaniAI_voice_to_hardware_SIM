"""The one place that talks to the LLM provider.

Switch provider in .env:  LLM_PROVIDER=groq  or  LLM_PROVIDER=gemini
                          (if only the other provider has a key, that one is used)
Keys: from the request (BYOK, see keys.py) or .env.
Optional overrides:       GROQ_MODEL=..., GEMINI_MODEL=..., LLM_REASONING=low|medium|high
Backup provider:          LLM_FALLBACK=auto (default: the other provider, if its key is set) | gemini | groq | none
Both providers expose an OpenAI-compatible chat endpoint, so one function covers both.

Rate limits: Groq's free tier allows about 8,000 tokens a minute for gpt-oss-120b, roughly one build
(understand + plan + explain) per minute. When the provider is busy (429/503), a timeout or a 5xx,
_post retries briefly or moves to the backup provider instead of failing the request. A rejected key
(401/403) also moves to the backup provider, if there is one.
"""
import json
import os
import re
import time

import httpx

import keys

PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-3.5-flash",  # the -latest alias often returned 503 "high demand"
    },
}
TIMEOUT_S = 30
QUICK_RETRY_S = 3    # a busy provider asking for at most this wait is simply retried
MAX_WAIT_S = 20      # with no backup provider, wait at most this long for the rate limit to reset


def retry_after_seconds(response):
    """Seconds the provider asks us to wait (Retry-After header), or 2 s if it does not say."""
    try:
        return max(0.5, float(response.headers.get("retry-after", "2")))
    except ValueError:
        return 2.0


class Busy(Exception):
    """The provider is rate limited, overloaded, timing out or failing (5xx): another try may work."""

    def __init__(self, message, wait, rejected_key=False):
        super().__init__(message)
        self.wait = wait
        self.rejected_key = rejected_key  # 401/403: waiting will not help, another key might


class LLMError(Exception):
    """bad_output=True: the model answered but not with usable JSON (a fix attempt may help).
    bad_output=False: the provider failed (network, auth, rate limit): retrying won't help."""

    def __init__(self, message, bad_output):
        super().__init__(message)
        self.bad_output = bad_output


def provider_info():
    """(provider, model) for this request: LLM_PROVIDER, or the other provider when only it has a key."""
    name = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    if name not in PROVIDERS:
        raise LLMError(f"Unknown LLM_PROVIDER '{name}' (use groq or gemini)", bad_output=False)
    if not keys.get(name):
        name = next((n for n in PROVIDERS if keys.get(n)), name)
    p = PROVIDERS[name]
    return name, os.getenv(p["model_env"]) or p["default_model"]


def has_key():
    """Is there a key for at least one LLM provider (the user's, or .env when allowed)?"""
    return any(keys.get(n) for n in PROVIDERS)


def fallback_provider(primary):
    """The backup provider to use when the primary is busy, or None."""
    choice = os.getenv("LLM_FALLBACK", "auto").strip().lower()
    if choice in ("", "none", "off"):
        return None
    other = next(n for n in PROVIDERS if n != primary) if choice == "auto" else choice
    if other == primary or other not in PROVIDERS or not keys.get(other):
        return None
    return other


def _call(name, messages):
    """One request to one provider. Raises Busy (worth another try) or LLMError (it is not)."""
    p = PROVIDERS[name]
    key = keys.get(name)
    if not key:
        message = (keys.missing_message(name) if has_key()
                   else "no LLM API key: add a Groq or Gemini key with the 🔑 API keys button"
                   + ("" if keys.user_keys_required() else " (or set one in .env)"))
        raise LLMError(message, bad_output=False)
    body = {
        "model": os.getenv(p["model_env"]) or p["default_model"],
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "reasoning_effort": os.getenv("LLM_REASONING", "low"),
    }
    try:
        r = httpx.post(p["url"], json=body, timeout=TIMEOUT_S, headers={"Authorization": f"Bearer {key}"})
    except httpx.TimeoutException:
        raise Busy(f"{name} did not answer within {TIMEOUT_S} s", wait=float("inf"))
    except httpx.HTTPError as e:
        raise Busy(f"could not reach {name}: {e}", wait=float("inf"))
    if r.status_code == 429:
        raise Busy(f"{name} rate limit reached", wait=retry_after_seconds(r))
    if r.status_code in (401, 403):
        raise Busy(f"{name} rejected the API key (HTTP {r.status_code}); check your {keys.LABELS[name]} key",
                   wait=float("inf"), rejected_key=True)
    if r.status_code >= 500:
        raise Busy(f"{name} returned HTTP {r.status_code} (busy or down)", wait=retry_after_seconds(r))
    if r.status_code != 200:
        raise LLMError(f"{name} returned HTTP {r.status_code}: {r.text[:300]}", bad_output=False)
    return r.json()["choices"][0]["message"].get("content") or ""


def _post(messages):
    """Ask the configured provider; when it is busy, retry briefly or use the backup provider."""
    primary, _ = provider_info()
    try:
        return _call(primary, messages)
    except Busy as e:
        busy = e
    backup = fallback_provider(primary)
    if busy.wait <= QUICK_RETRY_S or (not backup and busy.wait <= MAX_WAIT_S):
        time.sleep(busy.wait)
        try:
            return _call(primary, messages)
        except Busy as e:
            busy = e
    if backup:
        print(f"[llm] {busy}; using the backup provider {backup}", flush=True)  # shows in the uvicorn window
        try:
            return _call(backup, messages)
        except Busy as e:
            raise LLMError(f"{busy}; backup {e}" + ("" if e.rejected_key else "; please try again in a minute"),
                           bad_output=False)
    raise LLMError(f"{busy}" + ("" if busy.rejected_key else "; please try again in a minute"), bad_output=False)


def parse_json(text):
    """Strip code fences and parse. Returns a dict or None."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Some models add a sentence around the JSON; take the outermost {...}.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def chat_json(system, user, required_keys=()):
    """Send one request and return the parsed JSON dict.
    Re-asks once if the answer is not valid JSON or misses required keys."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        start = time.perf_counter()
        text = _post(messages)
        data = parse_json(text)
        missing = [k for k in required_keys if data is not None and k not in data]
        if data is not None and not missing:
            data["_llm_ms"] = round((time.perf_counter() - start) * 1000)
            return data
        problem = "was not valid JSON" if data is None else f"was missing keys {missing}"
        messages += [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": f"Your answer {problem}. Return valid JSON only, with keys {list(required_keys)}."},
        ]
    raise LLMError(f"the model's answer {problem} twice", bad_output=True)
