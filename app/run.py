import ctypes
import os
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA, DOWNLOADS

LOG = DATA / "organizer.log"

def single_instance():
    """Exit if another copy is already running: the autostart task and a manual
    run would race each other for the same downloads."""
    if os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    # The handle is never closed; Windows releases it when this process exits.
    kernel32.CreateMutexW(None, False, "Local\\ml-download-organizer")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        print("already running, exiting")
        sys.exit(0)

class _Timestamped:
    """Prefixes every line written to the log with the date and time."""
    def __init__(self, f):
        self.f, self.at_line_start = f, True

    def write(self, s):
        for line in s.splitlines(keepends=True):
            if self.at_line_start:
                self.f.write(time.strftime("%Y-%m-%d %H:%M:%S  "))
            self.f.write(line)
            self.at_line_start = line.endswith("\n")
        return len(s)

    def __getattr__(self, name):
        return getattr(self.f, name)

def log_to_file_if_headless():
    """pythonw (used by the autostart task) has no console, so sys.stdout is
    None and every print would vanish. Send output and tracebacks to a log."""
    if sys.stdout is not None:
        return
    DATA.mkdir(exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 5_000_000:
        os.replace(LOG, LOG.with_name(LOG.name + ".1"))
    f = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _Timestamped(f)

single_instance()
log_to_file_if_headless()

# At login the drive holding downloads_dir may not be mounted yet.
if not DOWNLOADS.is_dir():
    print(f"waiting for {DOWNLOADS} to become available...")
    while not DOWNLOADS.is_dir():
        time.sleep(30)

from app import indexer, mover, notifier, watcher

def reindex_loop():
    hours = indexer.CFG.get("reindex_hours", 24)
    idx = indexer.DATA / "index.faiss"
    while True:
        age = time.time() - idx.stat().st_mtime if idx.exists() else float("inf")
        time.sleep(max(60, hours * 3600 - age))
        try:
            indexer.build()
            print("background re-index done")
        except Exception as e:
            print("re-index failed:", e)

watcher.use_utf8_stdout()

# Without an index the first download would crash route(); build it up front.
if not (indexer.DATA / "index.faiss").exists():
    print("no index yet, building it now (first run)...")
    indexer.build()

threading.Thread(target=reindex_loop, daemon=True).start()

obs = watcher.start()
print(f"watching {watcher.DOWNLOADS}  (Ctrl+C to stop)")
if indexer.CFG.get("dry_run", True):
    print("DRY RUN: files will be logged but not moved")
try:
    while True:
        try:
            f = watcher.ready_queue.get(timeout=1)
        except queue.Empty:
            continue
        # One bad file (locked DB, unreadable PDF, ...) must not kill the daemon.
        try:
            result = mover.process(f)
            watcher.report(result)
            notifier.notify(result)
        except Exception as e:
            print(f"error processing {f}: {e}")
except KeyboardInterrupt:
    obs.stop()
obs.join()
