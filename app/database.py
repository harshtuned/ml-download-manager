import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "organizer.db"

def _run(sql, args=(), fetch=None):
    DB_PATH.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, args)
        conn.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.lastrowid

def init():
    _run("""
        CREATE TABLE IF NOT EXISTS moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            filename TEXT NOT NULL,
            src_path TEXT NOT NULL,
            dest_path TEXT,
            predicted_folder TEXT,
            score REAL,
            status TEXT NOT NULL,
            corrected_folder TEXT
        )
    """)

def log(filename, src, dest, predicted, score, status):
    return _run(
        "INSERT INTO moves (ts, filename, src_path, dest_path, predicted_folder,"
        " score, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), filename, src, dest,
         predicted, score, status),
    )

def last_moved():
    return _run(
        "SELECT * FROM moves WHERE status='moved' ORDER BY id DESC LIMIT 1",
        fetch="one",
    )

def set_status(row_id, status):
    # Update the status AND reset the clock to right now so the 60-second grace period works
    now = datetime.now().isoformat(timespec="seconds")
    _run("UPDATE moves SET status=?, ts=? WHERE id=?", (status, now, row_id))

def set_correction(row_id, folder):
    _run("UPDATE moves SET corrected_folder=? WHERE id=?", (folder, row_id))

def recent(n=50):
    return _run("SELECT * FROM moves ORDER BY id DESC LIMIT ?", (n,), fetch="all")

def recently_undone(src_path, seconds=60):
    row = _run(
        "SELECT ts FROM moves WHERE src_path=? AND status='undone'"
        " ORDER BY id DESC LIMIT 1",
        (src_path,), fetch="one",
    )
    if not row:
        return False
    age = datetime.now() - datetime.fromisoformat(row["ts"])
    return age < timedelta(seconds=seconds)