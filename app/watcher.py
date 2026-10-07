import sys
import json
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))  
CFG = json.loads((BASE / "config.json").read_text())

DOWNLOADS = Path(CFG["downloads_dir"]).expanduser().resolve()
TEMP_EXT = {e.lower() for e in CFG["temp_extensions"]}

ready_queue = queue.Queue()  
_pending = set()             
_lock = threading.Lock()

def is_candidate(path):
    p = Path(path)
    if p.parent.resolve() != DOWNLOADS:      
        return False
    name = p.name
    if name.startswith((".", "~$")):         
        return False
    if p.suffix.lower() in TEMP_EXT:
        return False
    return True

def is_unlocked(path):
    try:
        with open(path, "ab"):
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
    observer = Observer()
    observer.schedule(DownloadHandler(), str(DOWNLOADS), recursive=False)
    observer.start()
    return observer

if __name__ == "__main__":
    from app.mover import process

    obs = start()
    print(f"👀 Watching {DOWNLOADS}  (Ctrl+C to stop)")
    
    is_dry = CFG.get("dry_run", False)
    if is_dry:
        print("🟡 DRY RUN MODE ACTIVE: Files will be logged to database but not moved.")

    try:
        while True:
            try:
                file_path = ready_queue.get(timeout=1)
                result = process(file_path)
                
                if result:
                    status = result.get("status")
                    if status == "moved":
                        print(f"🚀 MOVED ({result['score']:.3f}): -> {result['folder']}")
                    elif status == "dry_run":
                        print(f"🧪 DRY RUN ({result['score']:.3f}): Would move to -> {result['folder']}")
                    elif status == "skipped_low_confidence":
                        print(f"🛑 SKIPPED ({result['score']:.3f}): Score too low. Left in Downloads.")
                    elif status == "ignored_after_undo":
                        print(f"⏪ IGNORED: File was just undone.")
                    elif status == "error":
                        print(f"❌ ERROR: {result['error']}")
                    
            except queue.Empty:
                pass
            except Exception as e:
                print(f"⚠ Error processing file: {e}")
                
    except KeyboardInterrupt:
        print("\nStopping watcher...")
        obs.stop()
    obs.join()