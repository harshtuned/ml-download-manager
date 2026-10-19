import os
import sys
import json
import threading
import numpy as np
import faiss
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CFG, DATA, DOWNLOADS, is_hidden, norm

# Loaded on first use: importing torch + the model takes seconds, and undo,
# list and the notification buttons never need it.
_model = None
_model_lock = threading.Lock()

def embed(texts):
    global _model
    if not texts:
        return None
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            print(f"Loading SentenceTransformer model: {CFG['model_name']}...")
            _model = SentenceTransformer(CFG["model_name"])
    return _model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

def expand(text):
    return CFG.get("aliases", {}).get(text.lower(), text)

def clean(text):
    return text.replace("_", " ").replace("-", " ")

def sample_files(folder):
    limit = CFG.get("max_files_per_folder", 20)
    files = []
    for entry in folder.iterdir():
        if entry.is_file() and not is_hidden(entry):
            files.append(entry)
            if len(files) >= limit:
                break
    return files

def folder_text(folder):
    parts = folder.parts[-2:]
    return ", ".join(expand(p) for p in reversed(parts))

def iter_folders():
    # Windows names are case-insensitive: C:\ has "$Recycle.Bin", not "$RECYCLE.BIN".
    ignore = {d.lower() for d in CFG["ignore_dirs"]}
    seen = set()
    for root in CFG["roots"]:
        root = Path(root).expanduser()

        if not root.exists():
            continue

        for dirpath, dirnames, _ in os.walk(root, onerror=lambda e: None):
            key = norm(dirpath)
            if key in seen:          # already walked via an overlapping root
                dirnames[:] = []
                continue
            seen.add(key)
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in ignore and not d.startswith(".")]
            yield Path(dirpath), root

def build():
    folders, name_texts, file_texts, spans = [], [], [], []
    # As written and as resolved, in case downloads_dir is a junction/redirect.
    inbox = {norm(DOWNLOADS), norm(CFG["downloads_dir"])}

    for folder, root in iter_folders():
        # The downloads folder is the source, never a destination. Its centroid
        # would be built from unsorted downloads and attract every new file.
        if norm(folder) in inbox:
            continue
        try:
            files = sample_files(folder)
        except (PermissionError, OSError):
            continue
        start = len(file_texts)
        file_texts.extend(clean(f.stem) for f in files)
        spans.append((start, len(file_texts)))
        name_texts.append(folder_text(folder))
        folders.append((folder, root, len(files)))

    if not folders:
        print("no folders found under the configured roots; index not written")
        return

    print(f"found {len(folders)} folders, embedding...")
    name_vecs = embed(name_texts)
    file_vecs = embed(file_texts) if file_texts else None

    w = CFG["name_weight"]
    manifest, vectors = [], []
    for i, ((folder, root, n), (a, b)) in enumerate(zip(folders, spans)):
        if b > a and file_vecs is not None:
            c = w * name_vecs[i] + (1 - w) * file_vecs[a:b].mean(axis=0)
        else:
            c = name_vecs[i]
        c = c / np.linalg.norm(c)
        manifest.append({
            "id": i,
            "path": str(folder),
            "parent": str(folder.parent) if folder != root else None,
            "n_files": n,
        })
        vectors.append(c)

    _save(np.vstack(vectors).astype("float32"), manifest)
    print(f"indexed {len(manifest)} folders")

def load_index():
    index_path = DATA / "index.faiss"
    manifest_path = DATA / "folder_manifest.json"
    if not index_path.exists() or not manifest_path.exists():
        raise FileNotFoundError("Index not found. Run `python app/indexer.py` first.")
    
    index = faiss.read_index(str(index_path))
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return index, manifest

def _save(mat, manifest):
    """Atomic write so the router never reads a half-written index."""
    DATA.mkdir(exist_ok=True)
    index = faiss.IndexFlatIP(mat.shape[1])
    index.add(mat)
    # Per-writer temp names: the daemon's background re-index and a CLI
    # `correct` can save at the same time and must not clobber each other.
    tag = f"{os.getpid()}.{threading.get_ident()}.tmp"
    tmp_i = DATA / f"index.faiss.{tag}"
    tmp_m = DATA / f"folder_manifest.json.{tag}"
    faiss.write_index(index, str(tmp_i))
    tmp_m.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp_m, DATA / "folder_manifest.json")
    os.replace(tmp_i, DATA / "index.faiss")

def update_centroid(folder_path, vec):
    """Nudge one folder's centroid toward a correction vector."""
    index, manifest = load_index()
    key = norm(folder_path)
    row = next((m for m in manifest if norm(m["path"]) == key), None)
    if row is None:
        return False
    mat = index.reconstruct_n(0, index.ntotal)
    n = max(1, min(row["n_files"], CFG.get("max_files_per_folder", 20)))
    a = 1.0 / (n + 1)
    c = (1 - a) * mat[row["id"]] + a * vec
    mat[row["id"]] = c / np.linalg.norm(c)
    row["n_files"] += 1
    _save(mat.astype("float32"), manifest)
    return True

if __name__ == "__main__":
    build()