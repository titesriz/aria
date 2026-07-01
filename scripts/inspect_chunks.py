"""Temporary inspection script — prints all chunks from a given PDF source file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_rag.config import load_settings
from aria_rag.retriever import load_index

TARGET = "REG2A1_MS1.pdf"

settings = load_settings()
_, _, _, chunks = load_index(settings)

matches = [(i, c) for i, c in enumerate(chunks) if Path(c.source_path).name == TARGET]

print(f"Found {len(matches)} chunks from {TARGET}\n")
print("=" * 80)

for idx, chunk in matches:
    print(f"[chunk index {idx}]")
    print(chunk.content[:300])
    print("-" * 80)
