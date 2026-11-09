"""arduino-cli wrapper: Arduino code in, Intel HEX text out.

Every compile first passes code_safety_problem(): on a public server anyone can send code, and the
C++ preprocessor could otherwise read server files (#include "/etc/passwd" shows lines of the file in
the error message). The compiler also runs with an environment that holds no keys.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FQBN = "arduino:avr:uno"
MAX_ERROR_LINES = 15
# Project-local Arduino libraries (Servo). Kept inside D:\gnani instead of Documents\Arduino.
LIBRARIES_DIR = Path(__file__).resolve().parent.parent / "arduino" / "libraries"


# ---------- code safety (preprocessor guard) ----------

ALLOWED_INCLUDE = re.compile(r"include\s*<(Servo|Arduino)\.h>\s*")
ALLOWED_DIRECTIVES = {"", "define", "undef", "if", "ifdef", "ifndef", "elif", "else", "endif",
                      "include", "error", "warning"}
# Anywhere in the code (outside comments and strings): pragmas, include probes, inline assembly.
FORBIDDEN = re.compile(r"\b(_Pragma|__has_include(?:_next)?|asm|__asm__|__asm)\b")
RAW_PREFIXES = {"R", "u8R", "uR", "UR", "LR"}
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASS|CREDENTIAL|AUTH", re.IGNORECASE)


def preprocessor_view(code):
    """The code roughly as the preprocessor sees it when it looks for directives: line splices
    joined, the digraph %: and trigraph ??= read as #, comments replaced by spaces (keeping their
    line breaks, so nothing after a comment can hide a directive), and the inside of string, raw
    string and character literals blanked (text in them is never a directive)."""
    s = re.sub(r"\\\r?\n", "", code).replace("%:", "#").replace("??=", "#")
    out, i, n = [], 0, len(s)
    while i < n:
        if s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            end = n if j < 0 else j + 2
            out.append(" " + "\n" * s.count("\n", i, end))
            i = end
        elif s[i].isalpha() or s[i] == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            word = s[i:j]
            m = re.match(r'"([^()\\\s"]{0,16})\(', s[j:]) if word in RAW_PREFIXES else None
            if m:  # raw string R"delim( ... )delim"
                close = s.find(")" + m.group(1) + '"', j + m.end())
                i = n if close < 0 else close + len(m.group(1)) + 2
                out.append('""')
            else:
                out.append(word)
                i = j
        elif s[i] in "\"'":
            quote, j = s[i], i + 1
            while j < n and s[j] not in (quote, "\n"):
                j += 2 if s[j] == "\\" else 1
            out.append(quote * 2)
            i = j + 1 if j < n and s[j] == quote else j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def code_safety_problem(code):
    """A reason this code may not be compiled here, or None. Allows only the directives an Arduino
    sketch needs: #define/#undef, #if/#ifdef/#ifndef/#elif/#else/#endif, #error/#warning, and
    #include <Servo.h> or <Arduino.h>."""
    view = preprocessor_view(code)
    for line in view.splitlines():
        m = re.match(r"\s*#\s*(\w*)(.*)", line)
        if not m:
            continue
        directive, rest = m.group(1), m.group(1) + m.group(2)
        if directive not in ALLOWED_DIRECTIVES:
            return f"#{directive} is not allowed here; use only #define, #if/#ifdef and #include <Servo.h>"
        if directive == "include" and not ALLOWED_INCLUDE.fullmatch(rest):
            return f"#{rest.strip()} is not allowed here; only #include <Servo.h> (and <Arduino.h>)"
    bad = FORBIDDEN.search(view)
    if bad:
        return f"{bad.group(1)} is not allowed here"
    return None


def safe_env():
    """The environment for the compiler and simulator: everything except anything that looks like a key."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}


def compile_sketch(code, line_offset=0, fqbn=FQBN):
    """Compile code for a board (default: Uno). Returns {ok: True, hex} or {ok: False, error}.

    line_offset: number of lines the backend prepended (the #define block).
    Error line numbers are shifted back so they match the code the LLM wrote.
    """
    cli = shutil.which("arduino-cli")
    if not cli:
        return {"ok": False, "error": "arduino-cli not found on PATH"}
    problem = code_safety_problem(code)
    if problem:
        return {"ok": False, "error": problem}

    with tempfile.TemporaryDirectory(prefix="gnani_") as tmp:
        # arduino-cli needs the folder name to equal the .ino name.
        sketch_dir = Path(tmp) / "sketch"
        out_dir = Path(tmp) / "out"
        sketch_dir.mkdir()
        (sketch_dir / "sketch.ino").write_text(code, encoding="utf-8")

        try:
            result = subprocess.run(
                [cli, "compile", "--fqbn", fqbn, "--libraries", str(LIBRARIES_DIR),
                 "--output-dir", str(out_dir), str(sketch_dir)],
                capture_output=True, text=True, timeout=60, env=safe_env(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "compile timed out after 60 s"}

        hex_file = out_dir / "sketch.ino.hex"  # never the with_bootloader file
        if result.returncode == 0 and hex_file.exists():
            return {"ok": True, "hex": hex_file.read_text()}
        error = (result.stderr or result.stdout).replace(str(sketch_dir), "sketch")
        return {"ok": False, "error": clean_error(error, line_offset)}


def clean_error(text, line_offset):
    """Keep the useful compiler lines and fix line numbers for the #define block."""
    def shift(m):
        return f"sketch.ino:{max(1, int(m.group(1)) - line_offset)}:"
    text = re.sub(r"sketch[\\/]sketch\.ino:(\d+):", shift, text)
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("Error during build")]
    return "\n".join(lines[:MAX_ERROR_LINES]).strip()


def normalize_error(text):
    """Error text without numbers, used to spot the same error twice in a row."""
    return re.sub(r"\d+", "#", text or "").strip()
