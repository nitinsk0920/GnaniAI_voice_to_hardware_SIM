"""The one place that talks to the LLM provider.

Switch provider in .env:  LLM_PROVIDER=groq  or  LLM_PROVIDER=gemini
Optional overrides:       GROQ_MODEL=..., GEMINI_MODEL=..., LLM_REASONING=low|medium|high
Both providers expose an OpenAI-compatible chat endpoint, so one function covers both.
"""
import json
import os
import re
import time

import httpx

PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-3.5-flash",  # the -latest alias often returned 503 "high demand"
    },
}
TIMEOUT_S = 30


class LLMError(Exception):
    """bad_output=True: the model answered but not with usable JSON (a fix attempt may help).
    bad_output=False: the provider failed (network, auth, rate limit): retrying won't help."""

    def __init__(self, message, bad_output):
        super().__init__(message)
        self.bad_output = bad_output


def provider_info():
    name = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    if name not in PROVIDERS:
        raise LLMError(f"Unknown LLM_PROVIDER '{name}' (use groq or gemini)", bad_output=False)
    p = PROVIDERS[name]
    return name, os.getenv(p["model_env"]) or p["default_model"]


def _post(messages):
    name, model = provider_info()
    p = PROVIDERS[name]
    key = os.getenv(p["key_env"])
    if not key:
        raise LLMError(f"{p['key_env']} is missing from .env", bad_output=False)

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "reasoning_effort": os.getenv("LLM_REASONING", "low"),
    }
    try:
        r = httpx.post(p["url"], json=body, timeout=TIMEOUT_S,
                       headers={"Authorization": f"Bearer {key}"})
    except httpx.TimeoutException:
        raise LLMError(f"{name} did not answer within {TIMEOUT_S} s", bad_output=False)
    except httpx.HTTPError as e:
        raise LLMError(f"could not reach {name}: {e}", bad_output=False)

    if r.status_code == 429:
        raise LLMError(f"{name} rate limit reached; wait a minute or switch LLM_PROVIDER", bad_output=False)
    if r.status_code != 200:
        raise LLMError(f"{name} returned HTTP {r.status_code}: {r.text[:300]}", bad_output=False)
    return r.json()["choices"][0]["message"].get("content") or ""


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
