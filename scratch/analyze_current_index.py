"""Read-only survey of data/index/chunks.json: per-family, per-file chunk
counts and section=null ratio. Run before any ingestion this session to
establish the true 'before' state (SESSION_STATE.md's 25%-era numbers were
measured against this same file)."""
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from aria_rag.config import Settings
from aria_rag.corpus_mapping import classify_path, load_rules, to_relative_posix

settings = Settings()
chunks = json.loads((settings.index_dir / "chunks.json").read_text(encoding="utf-8"))
rules = load_rules()

by_family_file = defaultdict(lambda: defaultdict(lambda: {"n": 0, "null_section": 0}))
for c in chunks:
    sp = Path(c["source_path"])
    cls = classify_path(sp, settings.docs_dir, rules)
    rel = to_relative_posix(sp, settings.docs_dir)
    fam = c["doc_family"]
    d = by_family_file[fam][rel]
    d["n"] += 1
    if c["section"] is None:
        d["null_section"] += 1

for fam in sorted(by_family_file):
    files = by_family_file[fam]
    total_chunks = sum(v["n"] for v in files.values())
    total_null = sum(v["null_section"] for v in files.values())
    print(f"\n=== family={fam} : {len(files)} files, {total_chunks} chunks, {total_null} null-section ({total_null/total_chunks*100:.0f}%) ===")
    for rel, v in sorted(files.items()):
        pct = v["null_section"] / v["n"] * 100 if v["n"] else 0
        flag = "  <-- ALL NULL" if pct == 100 else ("  <-- mostly null" if pct > 50 else "")
        print(f"  {v['n']:5d} chunks, {pct:5.1f}% null  {rel}{flag}")
