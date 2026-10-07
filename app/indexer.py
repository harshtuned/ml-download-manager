import os
import json
import numpy as np
import faiss
from pathlib import Path
from sentence_transformers import SentenceTransformer

with open("config.json", "r", encoding="utf-8") as f:
    CFG = json.load(f)

print(f"Loading SentenceTransformer model: {CFG['model_name']}...")
model = SentenceTransformer(CFG["model_name"])

def embed(texts):
    if not texts:
        return None
    return model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

def expand(text):
    return CFG.get("aliases", {}).get(text.lower(), text)

def clean(text):
    return text.replace("_", " ").replace("-", " ")

def sample_files(folder):
    limit = CFG.get("max_files_per_folder", 20)
    files = []
    for entry in folder.iterdir():
        if entry.is_file() and not entry.name.startswith("."):
            files.append(entry)
            if len(files) >= limit:
                break
    return files

def folder_text(folder):
    parts = folder.parts[-2:]
    return ", ".join(expand(p) for p in reversed(parts))

def iter_folders():
    ignore = set(CFG["ignore_dirs"])
    for root in CFG["roots"]:
        root = Path(root).expanduser()
        
        if not root.exists():
            print(f"\n❌ ERROR: Cannot find root folder -> {root}")
            continue
        else:
            print(f"✅ Scanning root folder -> {root}")

        for dirpath, dirnames, _ in os.walk(root, onerror=lambda e: None):
            dirnames[:] = [d for d in dirnames if d not in ignore and not d.startswith(".")]
            yield Path(dirpath), root

def build():
    folders, name_texts, file_texts, spans = [], [], [], []

    for folder, root in iter_folders():
        try:
            files = sample_files(folder)
        except (PermissionError, OSError):
            continue
        start = len(file_texts)
        file_texts.extend(clean(f.stem) for f in files)
        spans.append((start, len(file_texts)))
        name_texts.append(folder_text(folder))
        folders.append((folder, root, len(files)))

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

    mat = np.vstack(vectors).astype("float32")
    index = faiss.IndexFlatIP(mat.shape[1])
    index.add(mat)

    Path("data").mkdir(exist_ok=True)
    faiss.write_index(index, "data/index.faiss")
    Path("data/folder_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"indexed {len(manifest)} folders")

def load_index():
    index_path = Path("data/index.faiss")
    manifest_path = Path("data/folder_manifest.json")
    if not index_path.exists() or not manifest_path.exists():
        raise FileNotFoundError("Index not found. Run indexer.py first.")
    
    index = faiss.read_index(str(index_path))
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return index, manifest

if __name__ == "__main__":
    build()