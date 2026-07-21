"""Faithful export: Notion "Golden Cases v2 — Dataset unifié" -> eval/golden_dataset.json.

The legacy golden_dataset.json was a lossy hand-simplified export that
INVENTED content (DG_E_HAUTEUR.pdf in UC-01, "H/2 min 6m" in UC-03,
"notamment le 8e" in UC-04 -- none of these appear in the Notion source).
It must never be hand-edited again. This script is the only thing allowed
to write eval/golden_dataset.json from now on; eval/golden_dataset_LEGACY.json
is kept as a read-only relative-non-regression sentinel only -- see
CLAUDE.md's "Golden dataset provenance" section for why no score from the
legacy file is meaningful on its own.

FIDELITY RULES (the whole point of this script):
  - No field invention, no summarization, no dropping fields.
  - Text fields are copied VERBATIM (Notion's plain_text, concatenated
    across rich-text runs) -- never reworded, truncated, or "cleaned up".
  - Empty in Notion -> null in JSON (or [] for the two array fields),
    never silently filled in with a guess.
  - Enum-shaped fields (type_cas, type_echec, answerable) are mapped
    through a closed, exhaustive table to the task's specified short
    codes -- an option value Notion has that isn't in the table is a
    FAILURE (raise), not a best-effort guess.

Usage:
  python scripts/export_golden_cases.py                     # live Notion API
  python scripts/export_golden_cases.py --replay FILE.json  # offline replay
                                                              # of a previously
                                                              # saved raw API
                                                              # response (see
                                                              # --save-raw)
  python scripts/export_golden_cases.py --save-raw FILE.json  # also dump the
                                                              # raw API pages
                                                              # for replay/audit

Requires NOTION_API_KEY in the environment (.env) for live mode -- an
internal-integration token with read access to the database shared with it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "eval" / "golden_dataset.json"
DEFAULT_LEGACY = ROOT / "eval" / "golden_dataset_LEGACY.json"
DEFAULT_DIFF_REPORT = ROOT / "eval" / "golden_export_diff.md"

# The data source ID for "Golden Cases v2 — Dataset unifié (Couverture +
# Régression)", confirmed via the Notion database's own <data-source> tag.
# Database page: https://app.notion.com/p/14bdec1c6ffc43ab9db367dc09fd61da
DEFAULT_DATA_SOURCE_ID = "02e935c9-376c-4859-80bb-b33195d91fa1"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

# ---------------------------------------------------------------------------
# Closed enum maps -- see module docstring: unmapped input is a hard failure,
# never a guess. Keys are the exact Notion option names observed in the
# database schema (fetched 2026-07-21); values are the task's specified
# short codes.
# ---------------------------------------------------------------------------
_TYPE_CAS_MAP = {
    "Couverture": "couverture",
    "Régression": "regression",
}
_TYPE_ECHEC_MAP = {
    "Retrieval [R]": "R",
    "Synthèse [S]": "S",
    "Mixte [R+S]": "mixte",
}
_ANSWERABLE_MAP = {
    "Oui": "oui",
    "Renvoi doc": "renvoi",
    "Non": "non",
}

# fiabilite and famille_attendue have no target enum specified by the task
# (unlike the three above) -- kept verbatim, Notion's own French labels,
# per the "no field invention" rule.


class MappingError(Exception):
    """A Notion value doesn't fit the task's closed enum -- fail loudly."""


class ConsistencyGuardError(Exception):
    """A case has fiabilite == 'Fabriqué - à refaire' AND validated == true."""


# ---------------------------------------------------------------------------
# Notion API client (stdlib only -- no new dependency for two HTTP calls)
# ---------------------------------------------------------------------------

def _notion_request(url: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code} for {url}: {detail}") from exc


def fetch_all_pages(data_source_id: str, token: str) -> list[dict[str, Any]]:
    """Paginate /v1/data_sources/{id}/query, returning every raw page object."""
    url = f"{NOTION_API_BASE}/data_sources/{data_source_id}/query"
    pages: list[dict[str, Any]] = []
    body: dict[str, Any] = {"page_size": 100}
    while True:
        resp = _notion_request(url, token, body)
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        body = {"page_size": 100, "start_cursor": resp["next_cursor"]}
    return pages


# ---------------------------------------------------------------------------
# Property-value extraction — one function per Notion property type,
# each returning the FIDELITY-RULE-compliant Python value (None for empty
# scalars, [] for empty array-shaped fields, verbatim text otherwise).
# ---------------------------------------------------------------------------

def _plain_text(rich_text_array: list[dict[str, Any]]) -> str | None:
    if not rich_text_array:
        return None
    text = "".join(item.get("plain_text", "") for item in rich_text_array)
    return text if text != "" else None


def _prop(page: dict[str, Any], name: str) -> dict[str, Any]:
    props = page.get("properties", {})
    if name not in props:
        raise MappingError(
            f"Page {page.get('id')}: expected property {name!r} not found in "
            f"Notion response (available: {sorted(props)}) -- schema drift, fix "
            f"this script rather than guessing."
        )
    return props[name]


def _title(page: dict[str, Any], name: str) -> str | None:
    return _plain_text(_prop(page, name).get("title", []))


def _rich_text(page: dict[str, Any], name: str) -> str | None:
    return _plain_text(_prop(page, name).get("rich_text", []))


def _select(page: dict[str, Any], name: str) -> str | None:
    value = _prop(page, name).get("select")
    return value["name"] if value else None


def _multi_select(page: dict[str, Any], name: str) -> list[str]:
    return [item["name"] for item in _prop(page, name).get("multi_select", [])]


def _checkbox(page: dict[str, Any], name: str) -> bool:
    return bool(_prop(page, name).get("checkbox", False))


def _map_enum(raw: str | None, table: dict[str, str], field: str, case_id: str) -> str | None:
    if raw is None:
        return None
    if raw not in table:
        raise MappingError(
            f"Case {case_id}: {field}={raw!r} is not in the closed mapping "
            f"{sorted(table)} -- Notion has a new option this script doesn't "
            f"know about. Add it to the map deliberately, don't guess."
        )
    return table[raw]


def _split_articles(raw: str | None) -> list[str]:
    """"Articles / ressources attendus" is free rich_text, comma-separated in
    every one of the 12 real rows observed (parenthetical annotations like
    "(à confirmer)" stay attached to their entry, not split out) -- verified
    against the live database on 2026-07-21, not assumed.
    """
    if raw is None:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Row parsing — the pure, unit-testable core
# ---------------------------------------------------------------------------

def parse_row(page: dict[str, Any]) -> dict[str, Any]:
    case_id = _title(page, "Cas")
    if not case_id:
        raise MappingError(f"Page {page.get('id')} has an empty 'Cas' (title) property -- unusable as a case id.")

    type_cas_raw = _select(page, "Type de cas")
    type_echec_raw = _select(page, "Type d'échec testé")
    answerable_raw = _select(page, "Answerable")
    validated = _checkbox(page, "Validé Charline")
    fiabilite = _select(page, "Fiabilité référence")

    return {
        "id": case_id,
        "persona": _select(page, "Persona"),
        "question": _rich_text(page, "Question"),
        "context": _rich_text(page, "Contexte"),
        "type_cas": _map_enum(type_cas_raw, _TYPE_CAS_MAP, "type_cas", case_id),
        "type_echec": _map_enum(type_echec_raw, _TYPE_ECHEC_MAP, "type_echec", case_id),
        "famille_attendue": _multi_select(page, "Famille attendue"),
        "articles_attendus": _split_articles(_rich_text(page, "Articles / ressources attendus")),
        "answerable": _map_enum(answerable_raw, _ANSWERABLE_MAP, "answerable", case_id),
        "reponse_attendue": _rich_text(page, "Réponse attendue"),
        "ressource_a_pointer": _rich_text(page, "Ressource à pointer"),
        "critere_reussite": _rich_text(page, "Critère de réussite"),
        "reproche_charline": _rich_text(page, "Reproche Charline (verbatim)"),
        "fiabilite": fiabilite,
        "validated": validated,
        "priorite": _select(page, "Priorité"),
    }


def check_consistency_guard(cases: list[dict[str, Any]]) -> None:
    violations = [
        c["id"] for c in cases
        if c["fiabilite"] == "Fabriqué - à refaire" and c["validated"] is True
    ]
    if violations:
        raise ConsistencyGuardError(
            f"{len(violations)} case(s) are marked fiabilite='Fabriqué - à refaire' "
            f"(fabricated, needs a rewrite) AND validated=true (Charline checked "
            f"the box) at the same time: {violations}. That's a contradiction -- "
            f"a fabricated reference cannot also be validated. Refusing to export "
            f"until Notion is fixed (uncheck 'Validé Charline' or change "
            f"'Fiabilité référence')."
        )


# ---------------------------------------------------------------------------
# Diff report — old (legacy) vs new
# ---------------------------------------------------------------------------

def build_diff_report(legacy: list[dict[str, Any]], new: list[dict[str, Any]]) -> str:
    legacy_by_id = {c["id"]: c for c in legacy}
    new_by_id = {c["id"]: c for c in new}
    legacy_ids, new_ids = set(legacy_by_id), set(new_by_id)

    added = sorted(new_ids - legacy_ids)
    dropped = sorted(legacy_ids - new_ids)
    common = sorted(legacy_ids & new_ids)

    lines = [
        "# Golden dataset export diff — legacy vs Notion v2",
        "",
        f"- Legacy cases: {len(legacy)}",
        f"- New (Notion v2) cases: {len(new)}",
        f"- Added (in Notion v2, absent from legacy): {added or 'none'}",
        f"- Dropped (in legacy, absent from Notion v2): {dropped or 'none'}",
        "",
        "## Changed expectations (question or articles_attendus differs)",
        "",
    ]
    any_changed = False
    for cid in common:
        old, new_c = legacy_by_id[cid], new_by_id[cid]
        old_articles = old.get("expected_articles") or [
            item["value"] for item in old.get("expected", []) if item.get("type") == "article"
        ]
        new_articles = new_c.get("articles_attendus", [])
        old_q, new_q = old.get("question", ""), new_c.get("question", "")
        if old_articles == new_articles and old_q == new_q:
            continue
        any_changed = True
        lines.append(f"### {cid}")
        if old_q != new_q:
            lines.append(f"- question (legacy): {old_q!r}")
            lines.append(f"- question (v2): {new_q!r}")
        if old_articles != new_articles:
            lines.append(f"- expected articles (legacy): {old_articles!r}")
            lines.append(f"- articles_attendus (v2): {new_articles!r}")
        lines.append("")
    if not any_changed:
        lines.append("(none of the common cases have identical question+articles between the two files)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def export(
    data_source_id: str = DEFAULT_DATA_SOURCE_ID,
    replay_path: Path | None = None,
    save_raw_path: Path | None = None,
    output_path: Path = DEFAULT_OUTPUT,
    legacy_path: Path = DEFAULT_LEGACY,
    diff_report_path: Path = DEFAULT_DIFF_REPORT,
    write: bool = True,
) -> list[dict[str, Any]]:
    if replay_path is not None:
        raw_pages = json.loads(replay_path.read_text(encoding="utf-8"))
    else:
        load_dotenv()
        token = os.getenv("NOTION_API_KEY")
        if not token:
            raise SystemExit(
                "NOTION_API_KEY not set (.env or environment) — required for live "
                "export. Use --replay to run against a previously saved response."
            )
        raw_pages = fetch_all_pages(data_source_id, token)

    if save_raw_path is not None:
        save_raw_path.write_text(json.dumps(raw_pages, ensure_ascii=False, indent=2), encoding="utf-8")

    cases = [parse_row(page) for page in raw_pages]
    check_consistency_guard(cases)
    cases.sort(key=lambda c: c["id"])

    if write:
        if legacy_path is not None and output_path.exists() and not legacy_path.exists():
            legacy_path.write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")

        legacy_cases: list[dict[str, Any]] = []
        if legacy_path.exists():
            legacy_cases = json.loads(legacy_path.read_text(encoding="utf-8"))

        output_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        diff_report_path.write_text(build_diff_report(legacy_cases, cases), encoding="utf-8")

    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-source-id", default=DEFAULT_DATA_SOURCE_ID)
    parser.add_argument("--replay", type=Path, default=None, metavar="FILE", help="Replay a saved raw API response instead of hitting the network.")
    parser.add_argument("--save-raw", type=Path, default=None, metavar="FILE", help="Also save the raw API pages (for --replay / audit).")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--legacy", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--diff-report", type=Path, default=DEFAULT_DIFF_REPORT)
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate, but don't write any files.")
    args = parser.parse_args()

    cases = export(
        data_source_id=args.data_source_id,
        replay_path=args.replay,
        save_raw_path=args.save_raw,
        output_path=args.output,
        legacy_path=args.legacy,
        diff_report_path=args.diff_report,
        write=not args.dry_run,
    )

    validated = [c for c in cases if c["validated"]]
    pending = [c for c in cases if not c["validated"]]
    print(f"Exported {len(cases)} case(s): {[c['id'] for c in cases]}")
    print(f"  validated=true : {len(validated)} {[c['id'] for c in validated]}")
    print(f"  validated=false (pending expert review): {len(pending)} {[c['id'] for c in pending]}")
    print("NOTE: no score from this export is a certification until the 'Validé Charline' "
          "checkbox is set in Notion for the relevant case(s) — see the task's own scope note.")


if __name__ == "__main__":
    main()
