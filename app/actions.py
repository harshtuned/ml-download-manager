"""Handles clicks on notification buttons. Windows runs this for mldo: links,
e.g.  pythonw app/actions.py "mldo:undo?id=42"   (registered by notifier.py)
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database, notifier

ACTIONS = {"open", "undo", "accept", "correct"}

def parse(target):
    """'mldo:undo?id=42' -> ('undo', 42). Raises ValueError on anything else:
    links can come from outside (e.g. a web page), so accept nothing more."""
    parts = urlsplit(target.strip().rstrip("/"))   # Windows may append a "/"
    action = parts.path.strip("/")
    ids = parse_qs(parts.query).get("id", [])
    if parts.scheme != notifier.SCHEME or action not in ACTIONS or len(ids) != 1 \
            or not ids[0].isdigit():
        raise ValueError(f"bad link: {target}")
    return action, int(ids[0])

def pick_folder(title, start):
    import tkinter as tk
    from tkinter import filedialog
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp dialog on high-DPI screens
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(parent=root, title=title, mustexist=True,
                                       initialdir=start if os.path.isdir(start) else None)
    finally:
        root.destroy()

def reveal(path):
    p = Path(path)
    if p.exists():
        subprocess.Popen(f'explorer /select,"{p}"')
    elif p.parent.is_dir():
        os.startfile(p.parent)

def run(target):
    action, row_id = parse(target)
    row = database.get(row_id)
    if row is None:
        return

    if row["dest_path"] and row["status"] != "undone":
        current = row["dest_path"]
    else:
        current = row["src_path"]

    if action == "open":
        reveal(current)
        return

    from app import mover    # heavier import, only needed from here on
    if action == "undo":
        result = mover.undo(row_id)
    elif action == "accept":
        result = mover.correct(row_id, row["predicted_folder"])
    else:   # correct
        start = str(Path(row["predicted_folder"] or current).parent)
        folder = pick_folder(f"Where should {row['filename']} go?", start)
        if not folder:
            return      # dialog cancelled
        result = mover.correct(row_id, folder)
    notifier.notify(result)

if __name__ == "__main__":
    try:
        run(sys.argv[1] if len(sys.argv) > 1 else "")
    except Exception as e:
        # pythonw has no console, so the notification is the only place to say so.
        notifier.show(notifier.APP_NAME, f"Action failed: {e}")
