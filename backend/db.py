"""Tiny SQLite helpers: sessions and a log of runs (backend/gnani.db).
LangGraph's checkpointer keeps its own tables in the same file."""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "gnani.db"


def connect():
    # check_same_thread=False: FastAPI handles requests on worker threads.
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            request_text TEXT,
            circuit_json TEXT,
            code TEXT,
            hex TEXT,
            verdict TEXT,
            attempts INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def save_run(conn, session_id, request_text, circuit, code, hex_text, verdict, attempts):
    conn.execute("INSERT OR IGNORE INTO sessions (id) VALUES (?)", (session_id,))
    conn.execute(
        "INSERT INTO runs (session_id, request_text, circuit_json, code, hex, verdict, attempts)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, request_text, json.dumps(circuit), code, hex_text, verdict, attempts),
    )
    conn.commit()
