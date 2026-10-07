import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database
from app.indexer import CFG
from app.router import route

database.init()

def unique_path(dest_dir, name):
    target = dest_dir / name
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, suffix = target.stem, target.suffix
    cand = dest_dir / f"{stem}_{stamp}{suffix}"
    n = 1
    while cand.exists():
        cand = dest_dir / f"{stem}_{stamp}_{n}{suffix}"
        n += 1
    return cand

def process(path):
    src = Path(path)
    if not src.exists():
        return None

    if database.recently_undone(str(src)):
        return {"status": "ignored_after_undo", "file": src.name}

    folder, score = route(str(src))

    if folder is None or score < CFG.get("confidence_threshold", 0.50):
        database.log(src.name, str(src), None, folder, score, "skipped_low_confidence")
        return {"status": "skipped_low_confidence", "file": src.name,
                "folder": folder, "score": score}

    dest_dir = Path(folder)
    if not dest_dir.is_dir():
        database.log(src.name, str(src), None, folder, score, "error_missing_folder")
        return {"status": "error_missing_folder", "file": src.name, "folder": folder}

    if dest_dir.resolve() == src.parent.resolve():
        return {"status": "already_there", "file": src.name}

    if CFG.get("dry_run", True):
        database.log(src.name, str(src), None, folder, score, "dry_run")
        return {"status": "dry_run", "file": src.name, "folder": folder, "score": score}

    dest = unique_path(dest_dir, src.name)
    try:
        shutil.move(str(src), str(dest))
    except OSError as e:
        database.log(src.name, str(src), None, folder, score, f"error_{type(e).__name__}")
        return {"status": "error", "file": src.name, "error": str(e)}

    database.log(src.name, str(src), str(dest), folder, score, "moved")
    return {"status": "moved", "file": src.name, "folder": str(dest), "score": score}

def undo_last():
    row = database.last_moved()
    if not row:
        return None
    moved = Path(row["dest_path"])
    origin = Path(row["src_path"])
    if not moved.exists():
        database.set_status(row["id"], "undo_failed_missing")
        return {"status": "undo_failed_missing", "file": row["filename"]}

    target = unique_path(origin.parent, origin.name)
    shutil.move(str(moved), str(target))
    database.set_status(row["id"], "undone")
    return {"status": "undone", "file": row["filename"], "restored_to": str(target)}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "undo":
        print(undo_last())
    else:
        print(process(" ".join(sys.argv[1:])))