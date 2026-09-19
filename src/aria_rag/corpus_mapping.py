"""Config-driven corpus classification — replaces the hardcoded
DOC_FAMILIES dict (indexer.py) with a versionable YAML file
(corpus_mapping.yaml at repo root) of ordered folder/file-prefix rules.
See that file's own header comment for the schema.

Stage A of the CCH dual-source prototype: this module is the
"config-driven mapping" deliverable. norm_level/city ride along on Chunk
for a later stage to consume — nothing in this stage's retrieval path
reads them yet.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from aria_rag.config import ROOT_DIR

DEFAULT_CORPUS_MAPPING_PATH = ROOT_DIR / "corpus_mapping.yaml"


@dataclass(frozen=True, slots=True)
class MappingRule:
    prefix: str
    family: str
    norm_level: str | None
    city: str | None
    validity: str  # "current" | "superseded" | "excluded"


@dataclass(frozen=True, slots=True)
class Classification:
    family: str
    norm_level: str | None
    city: str | None
    validity: str


UNCLASSIFIED = Classification(family="other", norm_level=None, city=None, validity="current")


def load_rules(path: Path | None = None) -> list[MappingRule]:
    path = path or DEFAULT_CORPUS_MAPPING_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = []
    for entry in data.get("rules", []):
        rules.append(MappingRule(
            prefix=unicodedata.normalize("NFC", entry["prefix"]),
            family=entry["family"],
            norm_level=entry.get("norm_level"),
            city=entry.get("city"),
            validity=entry.get("validity", "current"),
        ))
    return rules


def _relative_posix(path: Path, docs_dir: Path) -> str:
    try:
        rel = path.resolve().relative_to(docs_dir.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return unicodedata.normalize("NFC", rel)


def to_relative_posix(path: Path, docs_dir: Path) -> str:
    """Public alias of `_relative_posix` — the one place path Unicode
    normalization (NFC) is meant to happen for this corpus. macOS's
    filesystem APIs hand back directory-entry strings in NFD for accented
    components ("Règlement", "Pièces écrites"); `classify_path` has always
    normalized through this before matching against corpus_mapping.yaml's
    (NFC) rule prefixes, so classification itself was never affected by
    that drift. But anything that PERSISTS a path string for later exact-
    equality comparison — e.g. indexer.py's `Chunk.source_path`, currently
    set to a raw `str(path)` with no normalization — is exactly the kind of
    comparison that silently breaks (see the 2026-09-18 REG1_MS1.pdf
    ingestion incident, where a naive `==` against an NFC literal matched
    zero of 666 existing NFD-stored chunks). Any code that needs to persist
    or compare a docs_dir-relative path should call this instead of adding
    its own ad hoc `unicodedata.normalize` — one normalization point,
    reused, rather than the same bug fixed per-script as it's rediscovered.
    """
    return _relative_posix(path, docs_dir)


def classify_path(path: Path, docs_dir: Path, rules: list[MappingRule]) -> Classification:
    """Longest-matching-prefix classification of `path` against `rules`.

    `path` is resolved relative to docs_dir (forward-slash, NFC-normalized)
    before matching. No match -> family "other", validity "current" — an
    unmapped file is still indexed (never silently dropped); it's the
    check suite's family-coverage invariant that turns an unexpected
    "other" chunk into a loud failure rather than this function.
    """
    rel = _relative_posix(path, docs_dir)

    best: MappingRule | None = None
    for rule in rules:
        if rel.startswith(rule.prefix) and (best is None or len(rule.prefix) > len(best.prefix)):
            best = rule
    if best is None:
        return UNCLASSIFIED
    return Classification(family=best.family, norm_level=best.norm_level, city=best.city, validity=best.validity)


def superseded_paths(docs_dir: Path, rules: list[MappingRule]) -> set[str]:
    """Relative (docs_dir-relative, forward-slash) paths of every rule
    whose validity is "superseded" — used by aria_rag.check's coverage
    invariant to avoid flagging an intentionally-excluded file as missing.
    Rule prefixes that are already a full file path (not a folder) are
    exactly the paths this returns; folder-level superseded rules aren't
    used today but are handled the same way if one is ever added.
    """
    return {rule.prefix for rule in rules if rule.validity == "superseded"}
