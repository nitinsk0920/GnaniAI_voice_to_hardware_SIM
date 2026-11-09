"""Bring your own key (BYOK): API keys that come with each request instead of from .env.

The browser keeps the visitor's keys and sends them as headers (X-Groq-Key, X-Gemini-Key,
X-Gnani-Key) with each request. They live only in a context variable for that request: never in
the agent state, the LangGraph config (its string values are copied into the SQLite checkpoint
metadata) or the logs.

Locally, a missing user key falls back to .env. On a public server set REQUIRE_USER_KEYS=1 so
the server's own keys are never used for visitors.
"""
import contextvars
import os

ENV_NAMES = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "gnani": "GNANI_API_KEY"}
HEADERS = {"groq": "x-groq-key", "gemini": "x-gemini-key", "gnani": "x-gnani-key"}
LABELS = {"groq": "Groq", "gemini": "Gemini", "gnani": "Gnani"}
MAX_KEY_LEN = 300

_user_keys = contextvars.ContextVar("user_keys", default={})


def user_keys_required():
    return os.getenv("REQUIRE_USER_KEYS", "").strip().lower() in ("1", "true", "yes", "on")


def clean(value):
    """A usable key, or "" (empty, too long, or with characters no key has)."""
    value = (value or "").strip()
    if not value or len(value) > MAX_KEY_LEN or not value.isascii() or any(c.isspace() for c in value):
        return ""
    return value


def from_headers(headers):
    """The user's keys from request headers: {"groq": ..., "gemini": ..., "gnani": ...} (missing ones left out)."""
    found = {name: clean(headers.get(header)) for name, header in HEADERS.items()}
    return {name: key for name, key in found.items() if key}


def get(name, explicit=None):
    """The key to use for a service: an explicit one, the request's user key, then .env (unless required)."""
    key = clean(explicit) or _user_keys.get().get(name, "")
    if not key and not user_keys_required():
        key = clean(os.getenv(ENV_NAMES[name]))
    return key


def missing_message(name):
    where = "" if user_keys_required() else f" or set {ENV_NAMES[name]} in .env"
    return f"no {LABELS[name]} API key: add yours with the 🔑 API keys button{where}"


def call(keys, fn, *args, **kwargs):
    """Run fn with these user keys in effect."""
    ctx = contextvars.copy_context()
    ctx.run(_user_keys.set, dict(keys))
    return ctx.run(fn, *args, **kwargs)


def iterate(keys, make_iterator):
    """Yield from make_iterator() with these user keys in effect for every step.
    A server streams a response one step at a time, often from different threads, so the keys are
    set in one private context and each step runs inside it (LangGraph's worker threads copy it)."""
    ctx = contextvars.copy_context()
    ctx.run(_user_keys.set, dict(keys))
    iterator = ctx.run(make_iterator)
    while True:
        try:
            item = ctx.run(next, iterator)
        except StopIteration:
            return
        yield item
