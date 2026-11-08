"""Compare measured pin data with the acceptance checks. Pure Python, no LLM.

measurements: {"duration_ms": N, "pins": {"9": [[time_ms, level], ...]}}
check: a dict from the LLM, already resolved by graph.py so it has "pin" (and "input_pin").
Every check may limit itself to a time window with "from_ms" and "to_ms".
"""
import statistics

SERVO_MIN_US, SERVO_MAX_US = 544, 2400  # Servo library defaults (checked in Servo.h 1.3.0)
DEFAULT_AFTER_MS = 200                  # ignore start-up


# ---------- signal helpers ----------

def window(edges, start, end):
    """Edges inside [start, end), plus the level at `start` as the first entry."""
    level = 0
    out = []
    for t, lv in edges:
        if t <= start:
            level = lv
        elif t < end:
            out.append((t, lv))
    return [(start, level)] + out


def duty(edges, start, end):
    """Fraction of time the pin is HIGH between start and end."""
    pts = window(edges, start, end)
    high = 0.0
    for (t0, lv), (t1, _) in zip(pts, pts[1:] + [(end, 0)]):
        if lv:
            high += t1 - t0
    return high / (end - start) if end > start else 0.0


def rising_edges(edges, start, end):
    return [t for (t, lv) in window(edges, start, end)[1:] if lv == 1]


def high_pulses(edges, start, end):
    """Widths (ms) of complete HIGH pulses that start in the window."""
    pts = window(edges, start, end)
    return [t1 - t0 for (t0, l0), (t1, l1) in zip(pts[1:], pts[2:]) if l0 == 1 and l1 == 0]


def duty_series(edges, start, end, step=50):
    """Duty in consecutive windows (50 ms is about 25 PWM cycles at 490 Hz)."""
    return [(t, duty(edges, t, t + step)) for t in range(int(start), int(end - step) + 1, step)]


def servo_angle(pulse_ms):
    return (pulse_ms * 1000 - SERVO_MIN_US) / (SERVO_MAX_US - SERVO_MIN_US) * 180


def fmt(x):
    return str(round(x))  # round() also turns -0.3 into 0


# ---------- check rules ----------

def check_steady(c, edges, a, b):
    pts = window(edges, a, b)
    changes = len(pts) - 1
    want = 1 if c["level"] == "high" else 0
    final = pts[-1][1]
    if changes == 0 and final == want:
        return True, f"pin {c['pin']} stayed {c['level'].upper()}"
    if changes:
        return False, f"pin {c['pin']} changed {changes} times between {fmt(a)} and {fmt(b)} ms; expected it to stay {c['level'].upper()}"
    return False, f"pin {c['pin']} stayed {'HIGH' if final else 'LOW'}; expected {c['level'].upper()}"


def check_blink(c, edges, a, b):
    rises = rising_edges(edges, a, b)
    lo, hi = c["period_ms_min"], c["period_ms_max"]
    if len(rises) < 2:  # need at least one full measured cycle
        return False, (f"pin {c['pin']} turned on only {len(rises)} times between {fmt(a)} and {fmt(b)} ms; "
                       f"expected a blink every {fmt(lo)}-{fmt(hi)} ms")
    period = statistics.median([y - x for x, y in zip(rises, rises[1:])])
    ok = lo <= period <= hi
    return ok, f"pin {c['pin']} blinks with a period of {fmt(period)} ms (expected {fmt(lo)}-{fmt(hi)} ms)"


def check_breathe(c, edges, a, b):
    series = duty_series(edges, a, b)
    if not series:
        return False, "the time window is too short to measure breathing"
    duties = [d for _, d in series]
    lo, hi = min(duties), max(duties)
    problems = []
    if lo > c["min_duty_below"]:
        problems.append(f"dimmest was {lo:.0%}, expected below {c['min_duty_below']:.0%}")
    if hi < c["max_duty_above"]:
        problems.append(f"brightest was {hi:.0%}, expected above {c['max_duty_above']:.0%}")

    # Period: time between upward crossings of the middle brightness (with hysteresis).
    mid, band = (lo + hi) / 2, max(0.05, (hi - lo) / 6)
    ups, below = [], False
    for t, d in series:
        if d < mid - band:
            below = True
        elif d > mid + band and below:
            ups.append(t)
            below = False
    p_lo, p_hi = c["period_ms_min"], c["period_ms_max"]
    if len(ups) < 2:
        problems.append(f"no repeating fade cycle seen between {fmt(a)} and {fmt(b)} ms (expected one every {fmt(p_lo)}-{fmt(p_hi)} ms)")
        period_text = "no full cycle"
    else:
        period = statistics.median([y - x for x, y in zip(ups, ups[1:])])
        period_text = f"cycle {fmt(period)} ms"
        if not p_lo <= period <= p_hi:
            problems.append(f"one fade cycle took {fmt(period)} ms, expected {fmt(p_lo)}-{fmt(p_hi)} ms")
    summary = f"pin {c['pin']} brightness ranged {lo:.0%} to {hi:.0%}, {period_text}"
    return (not problems), summary + ("" if not problems else "; " + "; ".join(problems))


def check_duty(c, edges, a, b):
    d = duty(edges, a, b)
    ok = c["min"] <= d <= c["max"]
    return ok, f"pin {c['pin']} was on {d:.0%} of the time between {fmt(a)} and {fmt(b)} ms (expected {c['min']:.0%}-{c['max']:.0%})"


def check_servo_angle(c, edges, a, b):
    pulses = [p for p in high_pulses(edges, a, b) if 0.3 <= p <= 3.0]
    if len(pulses) < 3:
        return False, f"no servo pulses on pin {c['pin']} between {fmt(a)} and {fmt(b)} ms (is the servo attached?)"
    angle = servo_angle(statistics.median(pulses))
    ok = abs(angle - c["angle_deg"]) <= c.get("tolerance_deg", 5)
    return ok, f"servo on pin {c['pin']} held {fmt(angle)} degrees (expected {fmt(c['angle_deg'])} ± {fmt(c.get('tolerance_deg', 5))})"


def check_servo_sweep(c, edges, a, b):
    angles = [servo_angle(p) for p in high_pulses(edges, a, b) if 0.3 <= p <= 3.0]
    if len(angles) < 3:
        return False, f"no servo pulses on pin {c['pin']} between {fmt(a)} and {fmt(b)} ms (is the servo attached?)"
    lo, hi = min(angles), max(angles)
    ok = lo <= c["min_angle_below"] and hi >= c["max_angle_above"]
    return ok, (f"servo on pin {c['pin']} moved between {fmt(lo)} and {fmt(hi)} degrees "
                f"(expected below {fmt(c['min_angle_below'])} and above {fmt(c['max_angle_above'])})")


def check_tone(c, edges, a, b):
    rises = rising_edges(edges, a, b)
    if len(rises) < 5:
        return False, f"no sound on pin {c['pin']} between {fmt(a)} and {fmt(b)} ms (expected {fmt(c['freq_min'])}-{fmt(c['freq_max'])} Hz)"
    freq = 1000 / statistics.median([y - x for x, y in zip(rises, rises[1:])])
    ok = c["freq_min"] <= freq <= c["freq_max"]
    return ok, f"pin {c['pin']} played {freq:.0f} Hz (expected {fmt(c['freq_min'])}-{fmt(c['freq_max'])} Hz)"


RULES = {
    "steady": check_steady,
    "blink": check_blink,
    "breathe": check_breathe,
    "duty": check_duty,
    "servo_angle": check_servo_angle,
    "servo_sweep": check_servo_sweep,
    "tone": check_tone,
}


def evaluate(checks, measurements):
    """Returns {"passed": bool, "results": [{"check", "ok", "detail"}]}."""
    duration = measurements["duration_ms"]
    results = []
    for c in checks:
        a = c.get("from_ms", DEFAULT_AFTER_MS)
        b = min(c.get("to_ms", duration), duration)
        edges = measurements["pins"].get(str(c["pin"]), [])
        label = f"{c['type']} {c.get('target', '')}".strip()
        if b - a < 100:
            ok, detail = False, f"time window {fmt(a)}-{fmt(b)} ms is too short to measure"
        else:
            ok, detail = RULES[c["type"]](c, edges, a, b)
        window_text = "" if (a == DEFAULT_AFTER_MS and b == duration) else f" [{fmt(a)}-{fmt(b)} ms]"
        results.append({"check": label + window_text, "ok": ok, "detail": detail})
    return {"passed": all(r["ok"] for r in results) and bool(results), "results": results}
