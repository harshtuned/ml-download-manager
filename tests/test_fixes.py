"""Sandboxed regression tests for the bug fixes. Copies app/ into a temp
project with its own config.json, so the real DB, index and files are never
touched. Run from any directory:

    venv\\Scripts\\python.exe tests\\test_fixes.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
work = Path(tempfile.mkdtemp(prefix="organizer_test_"))
proj = work / "proj"
tree = work / "tree"
inbox = tree / "Inbox"
shutil.copytree(PROJECT / "app", proj / "app",
                ignore=shutil.ignore_patterns("__pycache__"))

for d in ["Courses/FLAT/PYQs", "Courses/Machine Learning", "Photos/Trips",
          "$Recycle.Bin/S-1-5-21", "Inbox/Sorted Already"]:
    (tree / d).mkdir(parents=True)
(tree / "Courses/FLAT/PYQs/flat_endsem_2022.txt").write_text("automata regular grammar")
(tree / "Courses/Machine Learning/linear_regression_notes.txt").write_text("gradient descent")
(tree / "Photos/Trips/IMG_2041.txt").write_text("beach")
(tree / "$Recycle.Bin/S-1-5-21/deleted_thing.txt").write_text("x")
(tree / "Courses/desktop.ini").write_text("[.ShellClassInfo]")
os.system(f'attrib +h +s "{tree / "Courses/desktop.ini"}"')

cfg = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
cfg.update(roots=[str(tree), str(tree / "Courses")],   # overlapping roots on purpose
           downloads_dir=str(inbox).replace("\\", "/"),
           confidence_threshold=0.0, dry_run=False, notifications=False)
(proj / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

os.chdir(work)                      # NOT the project dir: tests CWD independence
sys.path.insert(0, str(proj))

failures = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond else ""))
    if not cond:
        failures.append(name)

from app import config, indexer
check("model not loaded at import (fast undo/buttons)",
      "sentence_transformers" not in sys.modules)
check("config anchored to project", config.DATA == proj / "data")
check("desktop.ini treated as hidden", config.is_hidden(tree / "Courses/desktop.ini"))

indexer.build()
check("index written under project/data, not CWD",
      (proj / "data/index.faiss").exists() and not (work / "data").exists())
_, manifest = indexer.load_index()
paths = [config.norm(m["path"]) for m in manifest]
check("inbox excluded from destinations", config.norm(inbox) not in paths)
check("inbox subfolders still indexed", config.norm(inbox / "Sorted Already") in paths)
check("$Recycle.Bin ignored case-insensitively",
      not any("recycle" in p for p in paths), [p for p in paths if "recycle" in p])
check("overlapping roots not duplicated", len(paths) == len(set(paths)))
courses = next(m for m in manifest if config.norm(m["path"]) == config.norm(tree / "Courses"))
check("hidden desktop.ini not sampled", courses["n_files"] == 0, courses)

from app import database, mover, router, watcher
ghost = inbox / "ghost.pdf"
check("lock probe does not create missing files",
      not watcher.is_unlocked(ghost) and not ghost.exists())
(inbox / "desktop.ini").write_text("x")
os.system(f'attrib +h +s "{inbox / "desktop.ini"}"')
check("hidden files are not download candidates", not watcher.is_candidate(inbox / "desktop.ini"))
(inbox / "notes.pdf").write_text("x")
check("normal files are download candidates", watcher.is_candidate(inbox / "notes.pdf"))
(inbox / "notes.pdf").unlink()

check("process('') does nothing", mover.process("") is None)
check("process(<dir>) does nothing", mover.process(str(tree / "Photos")) is None)
check("dir still in place", (tree / "Photos/Trips/IMG_2041.txt").exists())

big = work / "big.txt"
with open(big, "w") as f:
    f.write("word " * 3_000_000)    # 15 MB
_, content = router.extract_text(big)
check("text extract is bounded", 0 < len(content) <= cfg["pdf_chars"], len(content))

f1 = inbox / "flat_pyq_2023.txt"
f1.write_text("previous year question paper automata")
r = mover.process(str(f1))
check("file moved", r and r["status"] == "moved" and not f1.exists(), r)

# Undo with a name collision in the inbox
f1.write_text("a different, newer download with the same name")
u = mover.undo_last()
restored = Path(u["restored_to"])
check("undo restored under a new name", u["status"] == "undone" and restored != f1, u)
r_id = database.recent(1)[0]["id"]
row = database.get(r_id)
check("db src_path points at restored file", Path(row["src_path"]) == restored, row["src_path"])
check("restored file ignored by watcher after undo",
      mover.process(str(restored))["status"] == "ignored_after_undo")

# Correct with sloppy path spelling; must still learn from it
target = str(tree / "Courses/Machine Learning").upper().replace("\\", "/") + "/"
c = mover.correct(r_id, target)
check("correct moves restored file", c["status"] == "corrected" and not restored.exists(), c)
check("centroid updated despite case/slash differences", c["centroid_updated"], c)

# Correct the same row again while a same-named decoy sits at src_path
decoy = inbox / restored.name
decoy.write_text("DECOY")
c2 = mover.correct(r_id, str(tree / "Photos"))
check("re-correct moves the corrected file, not the decoy",
      c2["status"] == "corrected" and decoy.exists() and decoy.read_text() == "DECOY", c2)

# --- notifications -----------------------------------------------------------
fa, fb = inbox / "a_notes.txt", inbox / "b_notes.txt"
fa.write_text("alpha")
fb.write_text("beta")
ra, rb = mover.process(str(fa)), mover.process(str(fb))
check("results carry their row id", ra.get("id") and rb.get("id") and ra["id"] != rb["id"],
      (ra, rb))
ua = mover.undo(ra["id"])
check("undo(id) restores that file, not the latest one",
      ua["status"] == "undone" and fa.exists() and not fb.exists(), ua)
check("undoing the same row twice is refused", mover.undo(ra["id"])["status"] == "error")

from xml.dom import minidom
from app import actions, notifier
x = notifier.toast_xml('Moved <a&b> "c\'.pdf', "→ PYQs", r"A:\GATE27\FLAT & co\PYQs",
                       actions=[("Undo", notifier.link("undo", 7))],
                       launch=notifier.link("open", 7))
try:
    doc = minidom.parseString(x)
    well_formed = True
except Exception:
    well_formed = False
check("toast XML is well-formed with special characters", well_formed, x)
texts = [t.firstChild.data for t in doc.getElementsByTagName("text")] if well_formed else []
check("toast shows folder name and full path",
      texts[1:] == ["→ PYQs", r"A:\GATE27\FLAT & co\PYQs"], texts)

shown = []
notifier.show = lambda *a, **k: shown.append((a, k))     # capture instead of popping up
notifier.notify({"status": "moved", "id": 3, "file": "x.pdf",
                 "folder": r"A:\GATE27\FLAT\PYQs"})
check("moved notification names the destination folder",
      shown and shown[0][0][1:] == ("→ PYQs", r"A:\GATE27\FLAT\PYQs")
      and [b for b, _ in shown[0][1]["actions"]] == ["Undo", "Wrong folder"], shown)
check("button link parses", actions.parse("mldo:undo?id=7") == ("undo", 7))
check("button link with trailing slash parses", actions.parse("mldo:undo?id=7/") == ("undo", 7))
for bad in ["mldo:rm?id=1", "http:undo?id=1", "mldo:undo?id=1;2", "mldo:undo",
            "mldo:undo?id=1&id=2", ""]:
    try:
        actions.parse(bad)
        rejected = False
    except ValueError:
        rejected = True
    check(f"rejects bad link {bad!r}", rejected)

indexer.CFG["roots"] = [str(work / "does_not_exist")]
indexer.build()
check("build with no folders does not crash or wipe index", (proj / "data/index.faiss").exists())

print("\nALL PASS" if not failures else f"\n{len(failures)} FAILED: {failures}")
os.chdir(tempfile.gettempdir())
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failures else 0)
