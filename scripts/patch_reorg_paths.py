"""One-off: repair manifest.json/chunks.json source_path fields after the
manual Ressources/ reorg (PLU bioclimatique/... -> PLU/75 Paris/PLU
Bioclimatique/...; root LEGITEXT... -> LEGIFRANCE/CCH/...).

Metadata-only path correction, no re-embedding, no content changes — same
category of operation as scripts/patch_doc_family.py. Restores the
incremental-ingest cache so a scoped `--family reglement_ecrit` rebuild only
touches reglement_ecrit files instead of silently reprocessing everything
(every source_path would otherwise miss the cache after the path change).
"""
import json
from pathlib import Path

manifest_path = Path('data/index/manifest.json')
chunks_path = Path('data/index/chunks.json')

manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
chunks = json.loads(chunks_path.read_text(encoding='utf-8'))

new_paths = [p.resolve() for p in Path('Ressources').rglob('*.pdf')]


def new_suffix(p: Path) -> str | None:
    s = str(p).replace('\\', '/')
    if 'PLU Bioclimatique/' in s:
        return s.split('PLU Bioclimatique/', 1)[1]
    return None


suffix_to_new = {}
legitext_new = None
for p in new_paths:
    suf = new_suffix(p)
    if suf is not None:
        suffix_to_new[suf] = str(p)
    elif p.name == 'LEGITEXT000006074096.pdf':
        legitext_new = str(p)

assert legitext_new is not None, "LEGITEXT file not found on disk under LEGIFRANCE/"


def old_to_new(old_path: str) -> str:
    s = old_path.replace('\\', '/')
    if 'PLU bioclimatique/' in s:
        suf = s.split('PLU bioclimatique/', 1)[1]
        return suffix_to_new[suf]
    if old_path.endswith('LEGITEXT000006074096.pdf'):
        return legitext_new
    raise KeyError(f"no mapping for {old_path!r}")


remapped_manifest = 0
for m in manifest:
    new_p = old_to_new(m['source_path'])
    if new_p != m['source_path']:
        m['source_path'] = new_p
        remapped_manifest += 1

remapped_chunks = 0
for c in chunks:
    new_p = old_to_new(c['source_path'])
    if new_p != c['source_path']:
        c['source_path'] = new_p
        remapped_chunks += 1

manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
chunks_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding='utf-8')

print(f"Remapped {remapped_manifest}/{len(manifest)} manifest entries")
print(f"Remapped {remapped_chunks}/{len(chunks)} chunk entries")
