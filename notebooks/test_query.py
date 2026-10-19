import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.indexer import load_index
from app.router import extract_text, query_vector, route

# Get query from terminal
query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "FLAT_EndSem_Paper_2023.pdf"

print(f"Searching index for: '{query}'...")

# Embed the query exactly as the router does (aliases, cleaning, PDF text)
index, manifest = load_index()
q_vec = query_vector(*extract_text(query)).astype("float32").reshape(1, -1)
scores, ids = index.search(q_vec, min(5, index.ntotal))

print("\n--- Top 5 Matches (raw similarity) ---")
for s, i in zip(scores[0], ids[0]):
    if i >= 0:
        print(f"{s:.3f}  {manifest[i]['path']}")

folder, score = route(query)
print(f"\n--- Router pick (after keyword boosts + descent) ---\n{score:.3f}  {folder}")
