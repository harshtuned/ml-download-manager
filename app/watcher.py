import sys
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CFG, DOWNLOADS, is_hidden

TEMP_EXT = {e.lower() for e in CFG["temp_extensions"]}

ready_queue = queue.Queue()
_pending = set()
_lock = threading.Lock()

def is_candidate(path):
    p = Path(path)
    if p.parent.resolve() != DOWNLOADS:
        return False
    if is_hidden(p):
        return False
    if p.suffix.lower() in TEMP_EXT:
        return False
    return True

def is_unlocked(path):
    # "r+b" fails if the file vanished; "ab" would silently re-create it empty.
    try:
        with open(path, "r+b"):
            return True
    except (PermissionError, OSError):
        return False

def wait_until_stable(path):
    p = Path(path)
    deadline = time.time() + CFG["max_wait_seconds"]
    last_size, same = -1, 0

    while time.time() < deadline:
        if not p.exists():
            return False
        try:
            size = p.stat().st_size
        except OSError:
            return False

        if size == last_size and size > 0 and is_unlocked(p):
            same += 1
            if same >= CFG["stable_checks"]:
                return True
        else:
            same = 0
        last_size = size
        time.sleep(CFG["poll_interval"])
    return False

def _worker(path):
    try:
        if wait_until_stable(path):
            ready_queue.put(str(path))
    finally:
        with _lock:
            _pending.discard(str(path))

def _submit(path):
    path = str(Path(path).resolve())
    if not is_candidate(path):
        return
    with _lock:
        if path in _pending:
            return
        _pending.add(path)
    threading.Thread(target=_worker, args=(path,), daemon=True).start()

class DownloadHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            _submit(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            _submit(event.dest_path)

def start():
    if not DOWNLOADS.is_dir():
        raise FileNotFoundError(f"downloads_dir does not exist: {DOWNLOADS}")
    observer = Observer()
    observer.schedule(DownloadHandler(), str(DOWNLOADS), recursive=False)
    observer.start()
    return observer

def use_utf8_stdout():
    """When output is redirected (Task Scheduler, `> log.txt`) Windows falls
    back to cp1252 and an emoji or non-Latin filename raises UnicodeEncodeError."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def report(result):
    if not result:
        return
    status = result.get("status")
    score = result.get("score") or 0.0
    if status == "moved":
        print(f"🚀 MOVED ({score:.3f}): {result['file']} -> {result['folder']}")
    elif status == "dry_run":
        print(f"🧪 DRY RUN ({score:.3f}): Would move {result['file']} -> {result['folder']}")
    elif status == "skipped_low_confidence":
        print(f"🛑 SKIPPED ({score:.3f}): {result['file']} - score too low, left in Downloads."
              f" Best guess: {result.get('folder')}")
    elif status == "ignored_after_undo":
        print(f"⏪ IGNORED: {result['file']} was just undone.")
    elif status == "already_there":
        print(f"📍 ALREADY THERE: {result['file']}")
    elif status == "error_missing_folder":
        print(f"❌ ERROR: predicted folder no longer exists: {result['folder']} (re-index needed)")
    elif status == "error":
        print(f"❌ ERROR: {result['file']}: {result['error']}")
    else:
        print(result)

if __name__ == "__main__":
    from app import notifier
    from app.mover import process

    use_utf8_stdout()
    obs = start()
    print(f"👀 Watching {DOWNLOADS}  (Ctrl+C to stop)")

    if CFG.get("dry_run", True):   # same default as mover.process
        print("🟡 DRY RUN MODE ACTIVE: Files will be logged to database but not moved.")

    try:
        while True:
            try:
                file_path = ready_queue.get(timeout=1)
                result = process(file_path)
                report(result)
                notifier.notify(result)
            except queue.Empty:
                pass
            except Exception as e:
                print(f"⚠ Error processing file: {e}")

    except KeyboardInterrupt:
        print("\nStopping watcher...")
        obs.stop()
    obs.join()
