import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from app.config import DATA
from app.indexer import CFG, clean, embed, load_index, expand

_index = None
_manifest = None
_children = None
_names = None
_loaded_mtime = 0.0

def reload():
    global _index, _manifest, _children, _names, _loaded_mtime
    # Take the mtime *before* reading: if a re-index lands mid-load, the next
    # call sees a newer mtime and reloads instead of keeping a stale copy.
    mtime = _index_mtime()
    _index, _manifest = load_index()
    _loaded_mtime = mtime
    _children = {}
    for m in _manifest:
        if m["parent"] is not None:
            _children.setdefault(m["parent"], []).append(m["id"])
    _names = [Path(m["path"]).name.lower() for m in _manifest]

def _index_mtime():
    try:
        return (DATA / "index.faiss").stat().st_mtime
    except FileNotFoundError:
        return None

def _ensure_loaded():
    if _index is None or _index_mtime() != _loaded_mtime:
        reload()

def extract_text(path):
    p = Path(path)
    name = re.sub(r"\(\d+\)", "", p.stem)
    name = clean(name)
    name = " ".join(expand(word) for word in name.split())

    content = ""
    ext = p.suffix.lower()
    limit = CFG.get("pdf_chars", 1500)
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            if reader.pages:
                content = reader.pages[0].extract_text() or ""
        elif ext in {".txt", ".md", ".csv"}:
            # Only read the head: a multi-GB CSV must not be loaded into RAM.
            # Extra slack because whitespace is collapsed below.
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(limit * 4)
    except Exception:
        content = ""

    content = re.sub(r"\s+", " ", content).strip()[:limit]
    return name, content

def query_vector(name, content):
    nv = embed([name or "file"])[0]
    if not content:
        return nv
    cv = embed([content])[0]
    w = CFG.get("filename_weight", 0.7)
    q = w * nv + (1 - w) * cv
    return q / np.linalg.norm(q)

def apply_boosts(scores, text):
    text = text.lower()
    for rule in CFG.get("keyword_boosts", []):
        if any(t in text for t in rule["query_terms"]):
            for i, fname in enumerate(_names):
                if any(t in fname for t in rule["folder_terms"]):
                    scores[i] += rule["bonus"]
    return scores

def route(path):
    _ensure_loaded()
    if _index.ntotal == 0:
        return None, 0.0

    name, content = extract_text(path)
    q = query_vector(name, content).astype("float32").reshape(1, -1)

    sims, ids = _index.search(q, _index.ntotal)
    scores = np.zeros(_index.ntotal, dtype="float32")
    scores[ids[0]] = sims[0]
    scores = apply_boosts(scores, f"{name} {content}")

    node = int(np.argmax(scores))

    while True:
        kids = _children.get(_manifest[node]["path"], [])
        if not kids:
            break
        ranked = sorted(kids, key=lambda k: scores[k], reverse=True)
        best = ranked[0]
        if scores[best] < scores[node] - CFG.get("descend_margin", 0.05):
            break
        if len(ranked) > 1 and scores[best] - scores[ranked[1]] < CFG.get("tie_margin", 0.02):
            break
        node = best

    return _manifest[node]["path"], float(scores[node])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python app/router.py <file path>")
    target = " ".join(sys.argv[1:])
    folder, score = route(target)
    print(f"{score:.3f}  ->  {folder}")