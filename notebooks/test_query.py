import sys
import json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

# Load config
with open("config.json", "r", encoding="utf-8") as f:
    CFG = json.load(f)

# Get query from terminal
query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "FLAT_EndSem_Paper_2023.pdf"

print(f"Searching index for: '{query}'...")

# Load model and data
model = SentenceTransformer(CFG["model_name"])
index = faiss.read_index("data/index.faiss")

with open("data/folder_manifest.json", "r", encoding="utf-8") as f:
    manifest = json.load(f)

# Embed query and search
q_vec = model.encode([query], normalize_embeddings=True).astype("float32")
scores, ids = index.search(q_vec, 5)

print("\n--- Top 5 Matches ---")
for s, i in zip(scores[0], ids[0]):
    print(f"{s:.3f}  {manifest[i]['path']}")