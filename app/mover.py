import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import database
from app.config import norm
from app.indexer import CFG, update_centroid
from app.router import extract_text, query_vector, route

database.init()

USAGE = """usage:
  python app/mover.py <file>                  route and move one file
  python app/mover.py list                    show the last 10 actions
  python app/mover.py undo                    undo the last move
  python app/mover.py correct <id> <folder>   move a logged file and learn from it"""

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
    # Files only. Path("") is "." and a directory here would be handed to
    # shutil.move, whose cross-drive fallback is copytree + rmtree.
    if not src.is_file():
        return None

    if database.recently_undone(str(src)):
        return {"status": "ignored_after_undo", "file": src.name}

    folder, score = route(str(src))

    # Every logged result carries its row id so notification buttons can act on it.
    if folder is None or score < CFG.get("confidence_threshold", 0.50):
        row_id = database.log(src.name, str(src), None, folder, score, "skipped_low_confidence")
        return {"status": "skipped_low_confidence", "id": row_id, "file": src.name,
                "folder": folder, "score": score}

    dest_dir = Path(folder)
    if not dest_dir.is_dir():
        row_id = database.log(src.name, str(src), None, folder, score, "error_missing_folder")
        return {"status": "error_missing_folder", "id": row_id, "file": src.name,
                "folder": folder}

    if dest_dir.resolve() == src.parent.resolve():
        return {"status": "already_there", "file": src.name}

    if CFG.get("dry_run", True):
        row_id = database.log(src.name, str(src), None, folder, score, "dry_run")
        return {"status": "dry_run", "id": row_id, "file": src.name, "folder": folder,
                "score": score}

    dest = unique_path(dest_dir, src.name)
    try:
        shutil.move(str(src), str(dest))
    except OSError as e:
        row_id = database.log(src.name, str(src), None, folder, score,
                              f"error_{type(e).__name__}")
        return {"status": "error", "id": row_id, "file": src.name, "error": str(e)}

    row_id = database.log(src.name, str(src), str(dest), folder, score, "moved")
    return {"status": "moved", "id": row_id, "file": src.name, "folder": str(dest.parent),
            "dest": str(dest), "score": score}

def undo_last():
    row = database.last_moved()
    return undo(row["id"]) if row else None

def undo(row_id):
    """Put a moved (or corrected) file back where it was downloaded."""
    row = database.get(row_id)
    if not row:
        return {"status": "error", "error": f"no row {row_id}"}
    if row["status"] not in ("moved", "corrected"):
        return {"status": "error", "file": row["filename"],
                "error": f"nothing to undo (status: {row['status']})"}
    moved = Path(row["dest_path"])
    origin = Path(row["src_path"])
    if not moved.exists():
        database.set_status(row["id"], "undo_failed_missing")
        return {"status": "undo_failed_missing", "file": row["filename"]}

    target = unique_path(origin.parent, origin.name)
    try:
        shutil.move(str(moved), str(target))
    except OSError as e:
        return {"status": "error", "file": row["filename"], "error": str(e)}
    database.set_undone(row["id"], str(target))
    return {"status": "undone", "file": row["filename"], "restored_to": str(target)}

def correct(row_id, right_folder):
    """Move a file to the right folder and nudge that folder's centroid."""
    row = database.get(row_id)
    if not row:
        return {"status": "error", "error": f"no row {row_id}"}

    # Once moved or corrected the file sits at dest_path; otherwise (skipped,
    # dry run, error, undone) it is still at src_path.
    if row["dest_path"] and row["status"] != "undone":
        current = Path(row["dest_path"])
    else:
        current = Path(row["src_path"])
    right = Path(os.path.abspath(os.path.expanduser(right_folder)))

    if not current.is_file():
        return {"status": "error", "error": f"file not found: {current}"}
    if not right.is_dir():
        return {"status": "error", "error": f"folder not found: {right}"}

    # Move first, learn second: the first embed() loads the model, which takes
    # seconds, and the user who just picked a folder shouldn't wait for it.
    if norm(current.parent) == norm(right):
        dest = current          # already there; just learn from it
    else:
        dest = unique_path(right, current.name)
        try:
            shutil.move(str(current), str(dest))
        except OSError as e:
            return {"status": "error", "file": current.name, "error": str(e)}

    database.mark_corrected(row_id, str(right), str(dest))
    learned = update_centroid(right, query_vector(*extract_text(dest)))
    return {"status": "corrected", "id": row_id, "file": current.name,
            "folder": str(right), "to": str(dest), "centroid_updated": learned}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "undo":
        print(undo_last())
    elif args and args[0] == "list":
        for r in database.recent(10):
            print(r["id"], r["status"], f'{(r["score"] or 0):.3f}',
                  r["filename"], "->", r["dest_path"] or r["predicted_folder"])
    elif len(args) >= 3 and args[0] == "correct" and args[1].isdigit():
        print(correct(int(args[1]), " ".join(args[2:])))
    elif args and args[0] != "correct":
        target = " ".join(args)
        print(process(target) or f"not a file: {target}")
    else:
        print(USAGE)