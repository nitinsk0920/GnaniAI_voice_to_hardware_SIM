"""Protection for a public server: requests per visitor (IP) and builds running at the same time.

Visitors bring their own API keys, so abuse cannot spend ours, but every build compiles and
simulates on this server's CPU. Off by default (local use); RATE_LIMIT=1 turns it on.

Behind a proxy (Hugging Face Spaces) the visitor's address is the first X-Forwarded-For entry.
A visitor can fake that header, so this is a brake against accidental floods, not a security wall.
"""
import collections
import os
import threading
import time

from fastapi import HTTPException

# kind: (requests, per seconds) for one visitor
LIMITS = {
    "run": (20, 600),       # agent requests (each: LLM calls, compile, simulation)
    "compile": (30, 600),   # Compile & Run by hand
    "voice": (60, 600),     # speech-to-text and spoken replies
    "live": (10, 600),      # Live mode connections
}
MAX_PARALLEL_BUILDS = int(os.getenv("MAX_PARALLEL_BUILDS", "3"))
BUILD_MAX_S = 300           # a build never takes this long (the agent gives up much earlier)

_hits = collections.defaultdict(collections.deque)   # (kind, ip) -> request times
_lock = threading.Lock()
_builds = {}                                          # token -> start time of a running build


def enabled():
    return os.getenv("RATE_LIMIT", "").strip().lower() in ("1", "true", "yes", "on")


def client_ip(headers, fallback=""):
    forwarded = headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or fallback or "unknown"


def check(kind, ip, now=None):
    """Count one request; HTTP 429 when this visitor made too many recently."""
    if not enabled():
        return
    count, window = LIMITS[kind]
    now = time.monotonic() if now is None else now
    with _lock:
        times = _hits[(kind, ip)]
        while times and times[0] <= now - window:
            times.popleft()
        if len(times) >= count:
            wait = int(times[0] + window - now) + 1
            raise HTTPException(status_code=429, detail=f"Too many requests from your network. Please try again in {wait} s.")
        times.append(now)
        if len(_hits) > 10_000:   # forget visitors with no recent requests
            for k in [k for k, v in _hits.items() if not v or v[-1] <= now - 3600]:
                del _hits[k]


def start_build(now=None):
    """Reserve a build slot; HTTP 503 when the server is already busy with other builds.
    A slot also expires after BUILD_MAX_S, so one that is never released (the visitor left before
    the response started) cannot block the server for good."""
    if not enabled():
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        for token in [t for t, started in _builds.items() if started <= now - BUILD_MAX_S]:
            del _builds[token]
        if len(_builds) >= MAX_PARALLEL_BUILDS:
            raise HTTPException(status_code=503, detail="The server is busy with other builds. Please try again in a minute.")
        token = object()
        _builds[token] = now
        return token


def end_build(token):
    if token is not None:
        with _lock:
            _builds.pop(token, None)
