"""arduino-cli wrapper: Arduino code in, Intel HEX text out."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FQBN = "arduino:avr:uno"
MAX_ERROR_LINES = 15
# Project-local Arduino libraries (Servo). Kept inside D:\gnani instead of Documents\Arduino.
LIBRARIES_DIR = Path(__file__).resolve().parent.parent / "arduino" / "libraries"


def compile_sketch(code, line_offset=0):
    """Compile code for the Uno. Returns {ok: True, hex} or {ok: False, error}.

    line_offset: number of lines the backend prepended (the #define block).
    Error line numbers are shifted back so they match the code the LLM wrote.
    """
    cli = shutil.which("arduino-cli")
    if not cli:
        return {"ok": False, "error": "arduino-cli not found on PATH"}

    with tempfile.TemporaryDirectory(prefix="gnani_") as tmp:
        # arduino-cli needs the folder name to equal the .ino name.
        sketch_dir = Path(tmp) / "sketch"
        out_dir = Path(tmp) / "out"
        sketch_dir.mkdir()
        (sketch_dir / "sketch.ino").write_text(code, encoding="utf-8")

        try:
            result = subprocess.run(
                [cli, "compile", "--fqbn", FQBN, "--libraries", str(LIBRARIES_DIR),
                 "--output-dir", str(out_dir), str(sketch_dir)],
                capture_output=True, text=True, timeout=60,
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
