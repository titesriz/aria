"""
CANONICAL sommaire extractor — consolidates every fix accumulated across the
REG1 -> REG2A1 -> REG2A10 -> RP_CHOIX -> Rapport_presentation_MS1 study into
ONE script. Replaces (logic-wise): extract_sommaire_tree.py,
extract_sommaire_tree_reg2.py, extract_sommaire_tree_reg2_a10.py,
extract_sommaire_tree_rp.py (anti-fragmentation fixes carried forward),
relative_level_engine.py + extract_sommaire_tree_relative.py (STALE column
logic dropped — this script's column/header pipeline is the current one).

Extraction only — does not touch project code, does not split/write any PDF,
nothing written to Notion.

## What's in here, and why one unified pipeline works across every document

Header-length + TOC-page detection is DISCOVERED per document (search first
~10 lines of a candidate page for a "sommaire"/"table des matières" marker;
disambiguate multiple candidates by which one is followed by real TOC-shaped
content), not assumed from a fixed line count. Verified to reproduce REG1's
original 5-line header exactly, REG2/REG2A10's 5 lines, and RP's 2-4 lines —
one discovery rule, not per-family constants.

Column reading is geometry-based (1 or 2 columns decided from BLOCK-level x0
clustering — a much cleaner signal than per-line x0, see
_decide_column_split's docstring), with an anti-fragmentation layer: PyMuPDF
sometimes splits one visual text row into several word-level "line" objects
at different x0 (confirmed on Rapport_presentation_MS1.pdf). Fix: bucket by
the BLOCK's own x0 (never an individual fragment's), and coalesce fragments
sharing a block + a y-band (within 1.5pt) back into one logical line before
grouping — this is what took that document's anchor match from 90% to 100%.

Level is computed by the RELATIVE engine (position in the sommaire, not
semantic weight): a numbering profile that recurs anywhere in the document
(PARTIE n, Annexe <roman>, Axe n, a single letter/roman token, an ALL-CAPS
heading) jumps back to its first-occurrence level; a NESTING profile (digit-
dot chains, lettered zone-codes) moves by the signed depth-delta vs. its own
last occurrence; an unnumbered/non-caps line inherits the previous level; the
first entry of the document is level 1.

Run: python3 scratch/extract_sommaire.py
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = Path(__file__).resolve().parent

TOC_TITLE_MARKERS = {"sommaire", "table des matieres"}


def is_toc_marker_line(line: str) -> bool:
    """True if `line` is a TOC-title marker, allowing for one extra rendering
    quirk: extreme letter-spacing that survives coalescing as space-separated
    single characters ("S O M M A I R E") rather than one word — confirmed on
    RP_EIE.pdf, which otherwise has a real sommaire wrongly reported as
    absent. Checked both with and without internal spaces collapsed, so a
    genuinely multi-word marker ("table des matieres") still only matches its
    own spaced form and isn't accidentally loosened."""
    key = strip_accents(line).lower().strip(" :")
    return key in TOC_TITLE_MARKERS or key.replace(" ", "") in TOC_TITLE_MARKERS


DOT_LEADER_RE = re.compile(r"\.(?:\s?\.){2,}")
# Matches a TOC ellipsis run in either rendering: consecutive dots ("....")
# or individually SPACED dots (". . . ."), the latter confirmed necessary on
# RP_DIAGNOSTIC.pdf (see looks_like_standalone_toc_entry). Shared here as a
# module-level constant since discover_toc_shape_start (below) needs the
# exact same shape test.


def looks_like_dense_toc_line(line: str) -> bool:
    """A STRICTER shape test than looks_like_toc_line (which also accepts a
    bare trailing digit alone, too loose for marker-independent detection):
    requires an actual dot-leader run AND the line's own trailing page
    number, together — "text ... <dot-leader> ... <page number>". A dot-
    leader is a distinctive typographic feature essentially unique to TOC
    rows; it almost never appears in ordinary running prose, which is what
    makes this safe to use as a PRIMARY (marker-independent) signal rather
    than just a continuation-page heuristic."""
    return bool(DOT_LEADER_RE.search(line)) and bool(re.search(r"\d\s*$", line.strip()))


# Running header/footer bands, excluded from column clustering. Top and
# bottom are tuned SEPARATELY, not one shared constant — confirmed they need
# different sizes: the top band must be wide enough to catch a title/marker
# row that can sit well below the physical top margin (RP's "SOMMAIRE",
# y0=73 on a 595pt page) without being swept into column bucketing; the
# bottom band must be narrow enough to NOT cut into genuine last-line-of-page
# content on a densely-packed page (REG1's TOC pages: real content extends to
# y0≈768 on an 842pt page — only 74pt from the bottom — while the actual
# footer sits at y0≈795, just 47pt from the bottom). A single 90pt constant
# for both directions worked for RP/RP_CHOIX but silently ate REG1's last
# 3-4 entries per page at the page boundary — confirmed via direct bbox
# inspection before narrowing the bottom band.
TOP_MARGIN_PT = 90
BOTTOM_MARGIN_PT = 60


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(s: str) -> str:
    s = strip_accents(s.lower())
    # De-hyphenate a PDF line-wrap ("cli-\nmatique" -> "climatique") before the
    # generic non-alnum strip below would otherwise turn it into "cli matique"
    # (two words) — confirmed necessary for anchor_check: RP_RNT.pdf's real
    # target pages wrap "climatique" and "reglementaire" exactly at a mid-word
    # hyphen, so the exact-substring match silently failed even though the
    # title genuinely appears on the page. Only a hyphen immediately followed
    # by a newline (not any same-line hyphen, e.g. a real compound word) is
    # treated as a wrap.
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_all_caps_header(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return all(c == c.upper() for c in letters) and any(c.isupper() for c in letters)


# ---------------------------------------------------------------------------
# Unified numbering-profile vocabulary — every profile encountered across
# REG1 / REG2A1 / REG2A10 / RP_CHOIX / Rapport_presentation_MS1.
# category: "FLAT" (recurs -> jumps back to first-occurrence level) or
# "NESTING" (moves by signed depth-delta vs. its own last occurrence).
# ---------------------------------------------------------------------------
RE_PARTIE = re.compile(r"^PARTIE\s+(\d+)\s*:?\s*(.*)$")
RE_ANNEXE = re.compile(r"^Annexe\s+([IVXLCDM]+)\s*:\s*(.*)$")
RE_PARTIE_ORDINAL = re.compile(r"^(\d+)(?:ère|ere|ème|eme|er|e)\s+partie\s*:\s*(.*)$", re.IGNORECASE)
RE_AXE = re.compile(r"^Axe\s+(\d+)\s*:\s*(.*)$", re.IGNORECASE)
RE_TABLEAU = re.compile(r"^Tableau\s+(\d+)\.\s*(.*)$")  # "Tableau 1. ..." — RP_INDIC/RP_EVAL's list-of-tables
RE_CODE_DOTGROUPS = re.compile(r"^([A-Z]{1,10}(?:\.\d+)+)\.?\s+(.*)$")  # UG.1.4, C.1, N.1.2.2
RE_ZONE_SUBCODE = re.compile(r"^([A-Z]{1,10})\.\s+(\d+(?:\.\d+)*)\.?\s+(.*)$")
# "UGSU. 2.1 ..." / "UGSU. 2.3. ..." — same zone-code family as RE_CODE_DOTGROUPS
# (UG.1.4, N.1.2.2), but the source PDF sometimes inserts a SPACE between the
# zone prefix's dot and its sub-number, inconsistently even within the same
# document (confirmed on RP_CHOIX.pdf: "UGSU. 2.1 Dispositions..." and "UGSU.
# 2.3. Dispositions..." both have the space; "UGSU.3.2. Hauteur..." doesn't
# and already matches RE_CODE_DOTGROUPS). Without this, RE_CODE_DOTGROUPS's
# `(?:\.\d+)+` can't match past the space, so parse_prefix fell through all
# the way to RE_LETTERS_DOT, which greedily captured just the zone prefix
# ("UGSU") as the whole code and left the real differentiating sub-number
# ("2.1"/"2.2"/"2.3"/"3.1"/"3.3") sitting in the title text — the cause of 5
# sibling entries all wrongly sharing numero='UGSU' under section 1.5.2. Must
# be tried BEFORE RE_LETTERS_DOT (which would otherwise win first, since
# "UGSU" also fits its 1-4-letter cap) and must require a mandatory digit
# after the space (never optional) so a genuine no-code header like "UGSU.
# Zone urbaine générale sud" (no following digits) still correctly falls
# through to RE_LETTERS_DOT exactly as before.
RE_DIGIT_DOT = re.compile(r"^(\d+(?:\.\d+)*)\.\s+(.*)$")  # 1., 1.1., 2.14. — trailing dot mandatory
RE_LETTERS_DOT = re.compile(r"^([A-Z]{1,4})\.\s+(.*)$")  # single letter or roman numeral
RE_ISOLATED_NUMBERING = re.compile(r"^\d+(?:\.\d+)*\.\s*$")  # bare "1." / "2.14." alone on its own line
RE_FUSED_PAGENUM_AND_NEXT_NUMBERING = re.compile(r"^(\d+)\s+(\d+(?:\.\d+)*\.?)\s*$")


def parse_prefix(line: str):
    """Returns (profile_key, category, raw_numbering, depth, remainder) or None."""
    m = RE_PARTIE.match(line)
    if m:
        return ("PARTIE", "FLAT", f"PARTIE {m.group(1)}", 1, m.group(2))
    m = RE_ANNEXE.match(line)
    if m:
        return ("ANNEXE", "FLAT", f"Annexe {m.group(1)}", 1, m.group(2))
    m = RE_PARTIE_ORDINAL.match(line)
    if m:
        return ("PARTIE_ORDINAL", "FLAT", f"{m.group(1)}e partie", 1, m.group(2))
    m = RE_AXE.match(line)
    if m:
        return ("AXE", "FLAT", f"Axe {m.group(1)}", 1, m.group(2))
    m = RE_TABLEAU.match(line)
    if m:
        return ("TABLEAU", "FLAT", f"Tableau {m.group(1)}", 1, m.group(2))
    m = RE_CODE_DOTGROUPS.match(line)
    if m:
        raw = m.group(1)
        depth = sum(1 for p in raw.split(".")[1:] if p.isdigit())
        return ("DOTCHAIN", "NESTING", raw, depth, m.group(2))
    m = RE_ZONE_SUBCODE.match(line)
    if m:
        raw = f"{m.group(1)}.{m.group(2)}"
        depth = sum(1 for p in raw.split(".")[1:] if p.isdigit())
        return ("DOTCHAIN", "NESTING", raw, depth, m.group(3))
    m = RE_DIGIT_DOT.match(line)
    if m:
        raw = m.group(1)
        depth = len(raw.split("."))
        return ("DOTCHAIN", "NESTING", raw, depth, m.group(2))
    m = RE_LETTERS_DOT.match(line)
    if m:
        return ("LETTERS_DOT", "FLAT", m.group(1), 1, m.group(2))
    return None


def classify_group_first_line(first_line: str, is_caps: bool):
    prefix = parse_prefix(first_line)
    if prefix:
        return prefix
    if is_caps:
        return ("UNNUMBERED_CAPS", "FLAT", None, 1, first_line)
    return ("UNNUMBERED_OTHER", "NONE", None, 1, first_line)


@dataclass
class ParsedLine:
    numbering_type: str
    category: str
    raw_numbering: str | None
    depth: int
    titre: str
    page_debut: int


def compute_relative_levels(parsed: list[ParsedLine]) -> list[int]:
    """Level built line-by-line, relative to the previous entry — see module
    docstring. First entry = level 1; a recurring FLAT profile jumps back to
    its first-occurrence level; a NESTING profile moves by the signed depth
    delta vs. its own last occurrence; an unnumbered/non-caps line inherits."""
    levels: list[int] = []
    type_first_level: dict[str, int] = {}
    current_level = 1
    current_type: str | None = None
    current_depth = 1

    for idx, entry in enumerate(parsed):
        if idx == 0:
            level = 1
            if entry.category != "NONE":
                type_first_level[entry.numbering_type] = level
                current_type = entry.numbering_type
                current_depth = entry.depth
            current_level = level
            levels.append(level)
            continue

        if entry.category == "NONE":
            level = current_level
        elif entry.category == "FLAT":
            if entry.numbering_type in type_first_level:
                level = type_first_level[entry.numbering_type]
            else:
                level = current_level + 1
                type_first_level[entry.numbering_type] = level
            current_type = entry.numbering_type
            current_depth = entry.depth
            current_level = level
        elif entry.category == "NESTING":
            if entry.numbering_type == current_type:
                level = current_level + (entry.depth - current_depth)
            else:
                level = current_level + 1
                type_first_level.setdefault(entry.numbering_type, level)
            current_type = entry.numbering_type
            current_depth = entry.depth
            current_level = level
        else:
            raise ValueError(entry.category)

        levels.append(level)

    return levels


# ---------------------------------------------------------------------------
# Generic running header/footer boilerplate filter (content-based, not
# position-based) — catches boilerplate that ends up MID-sequence after
# column reordering, which header-length slicing alone can't reach.
# ---------------------------------------------------------------------------
_BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"^\d+\s*/\s*\d+"),
    re.compile(r"^PLU (DE PARIS|APPROUVÉ)\b", re.IGNORECASE),
    re.compile(r"^[A-ZÉÈÀÂÎÔÛÇ]+\s+\d{4}\s*$"),
]


def is_boilerplate_line(line: str) -> bool:
    return any(p.match(line.strip()) for p in _BOILERPLATE_LINE_PATTERNS)


def clean_title_and_extract_page(raw_text: str):
    text = re.sub(r"\.{2,}", " ", raw_text)
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"(\d+)\s*$", text)
    if not m:
        return text.strip(" .:"), None
    page = int(m.group(1))
    title = text[: m.start()].strip(" .:")
    return title, page


def looks_like_toc_line(line: str) -> bool:
    if re.search(r"\.{2,}", line):
        return True
    if re.search(r"\d\s*$", line.strip()):
        return True
    if RE_ISOLATED_NUMBERING.match(line.strip()):
        return True
    return False


# ---------------------------------------------------------------------------
# Column-aware, header-length-agnostic, anti-fragmentation page-line reading.
# ---------------------------------------------------------------------------
def _merge_letter_spaced_runs(items):
    """
    Merge a run of consecutive single-ALPHABETIC-character tokens sharing a
    y-band (within 1.5pt) into one word — confirmed necessary on
    RP_EIE.pdf, whose "SOMMAIRE" title is rendered with extreme letter-
    spacing, one character per PyMuPDF line/block ("S","O","M","M","A","I",
    "R","E" as 8 separate tokens, same y0, spread across the page width).
    Without this, the exact-string marker match never fires and the document
    is wrongly reported as having no sommaire at all. A run must be >= 3
    single-character tokens to merge (a lone stray single letter, e.g. an
    initial in running prose, is never touched).
    """
    items = sorted(items, key=lambda r: (round(r[2]), r[1]))
    out = []
    i = 0
    while i < len(items):
        text, x0, y0 = items[i]
        if len(text) == 1 and text.isalpha():
            run = [items[i]]
            j = i + 1
            while (
                j < len(items)
                and len(items[j][0]) == 1
                and items[j][0].isalpha()
                and abs(items[j][2] - run[-1][2]) <= 1.5
            ):
                run.append(items[j])
                j += 1
            if len(run) >= 3:
                merged_text = "".join(t for t, _, _ in run)
                out.append((merged_text, run[0][1], run[0][2]))
                i = j
                continue
        out.append(items[i])
        i += 1
    return out


def get_page_lines_with_bbox(page):
    """Approximate per-line (text, x0, y0) triples — used only for the
    header/footer-margin split and the <6-lines short-circuit. NOT used for
    column bucketing (see get_page_lines_by_block)."""
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", []))
            if text.strip():
                x0, y0, _, _ = line["bbox"]
                out.append((text.strip(), x0, y0))
    return _merge_letter_spaced_runs(out)


def get_page_lines_by_block(page):
    """
    One entry per non-empty text BLOCK: (block_x0, block_y0, [line texts in
    that block's own order, coalesced]).

    Anti-fragmentation fix (took Rapport_presentation_MS1.pdf's anchor match
    from 90% to 100%): PyMuPDF sometimes splits one visual text row into
    several word-level "line" objects at different x0 (confirmed: "Correction
    d'une erreur de saisie concernant les" came back as 7 separate fragments,
    some crossing the column split point purely because the sentence is wide).

    Fix, part 1: bucket by the BLOCK's own x0 (a clean, reliable signal —
    collapses to two dominant clusters on real 2-column pages), never by an
    individual fragment's x0. All lines of a block travel together.

    Fix, part 2: coalesce fragments sharing a block AND a y-band (within
    1.5pt — tighter than a real line-height gap, so genuinely different
    physical lines of a wrapped entry are never merged) into one logical
    line, left-to-right by x0. Needed because a leftover mid-sentence number
    (e.g. a house number "79") could otherwise still falsely look like a
    trailing page number and close a group early.
    """
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        raw_lines = []
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", []))
            if text.strip():
                x0, y0, _, _ = line["bbox"]
                raw_lines.append((x0, y0, text.strip()))

        raw_lines.sort(key=lambda r: (r[1], r[0]))
        coalesced = []
        for x0, y0, text in raw_lines:
            if coalesced and abs(coalesced[-1][0] - y0) <= 1.5:
                prev_y0, prev_text = coalesced[-1]
                coalesced[-1] = (prev_y0, f"{prev_text} {text}")
            else:
                coalesced.append((y0, text))
        lines_text = [text for _, text in coalesced]
        if lines_text:
            x0, y0 = block["bbox"][0], block["bbox"][1]
            out.append((x0, y0, lines_text))
    return out


def get_page_block_geometry(page):
    """(x0, n_lines) per non-empty text BLOCK — n_lines is the column-balance
    signal used by _decide_column_split. Raw block COUNT is unreliable as a
    balance signal when one column happens to be authored as one large
    flowing text frame (few blocks, many lines) versus the other as several
    small boxes (confirmed necessary on RP_INDIC.pdf: its whole right column
    — an 8-entry "Tableau N." list — is a single ~10-line block, which a
    block-count floor rejected outright even though it plainly carries as
    much content as the left column's several smaller blocks)."""
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        n_lines = sum(
            1
            for line in block.get("lines", [])
            if "".join(s.get("text", "") for s in line.get("spans", [])).strip()
        )
        if n_lines:
            out.append((block["bbox"][0], n_lines))
    return out


def _decide_column_split(block_geometry, page_width):
    """1-vs-2-column decision from BLOCK-level x0 (single source of truth,
    used both by real reordering and the console report). Tries every x0
    gap, LARGEST FIRST — not just the single largest, which is what the
    original version did. A decorative one-off block sitting between the
    two real columns (e.g. a "SOMMAIRE" title placed at an x0 between them)
    can fragment the true column gap into two smaller ones, each of which
    individually fails the position/balance checks below even though the
    real split is obviously there once that block is looked past (confirmed
    necessary on RP_RNT.pdf). So: accept the first candidate gap, in
    descending size order, where the split lands away from the edges
    (25%-75% of page width) AND both sides carry a substantial, comparable
    LINE count (>=20% of the page's total lines, floor 5 — line count, not
    block count, see get_page_block_geometry's docstring). Landscape
    orientation is a WEAK hint only — decided purely by this geometry, never
    by page rotation/aspect. Returns (is_two_column, split_at)."""
    if len(block_geometry) < 6:
        return False, None

    total_lines = sum(n for _, n in block_geometry)
    min_side = max(5, int(0.2 * total_lines))

    xs = sorted(set(round(x0) for x0, _ in block_geometry))
    gaps = sorted(((b - a, (a + b) / 2) for a, b in zip(xs, xs[1:])), key=lambda g: -g[0])

    for gap, split_at in gaps:
        if gap <= 0.15 * page_width:
            break  # sorted descending — no smaller gap left is worth trying either
        if not (0.25 * page_width < split_at < 0.75 * page_width):
            continue
        left_n = sum(n for x0, n in block_geometry if x0 <= split_at)
        right_n = total_lines - left_n
        if left_n >= min_side and right_n >= min_side:
            return True, split_at

    return False, None


def get_ordered_page_lines(page, is_start_page=True):
    """Full page reading order: header band, then body (column-aware), in that
    order. Used only where the header/body split doesn't matter (the <6-lines
    short-circuit, and page_is_two_column). For actual content extraction, use
    get_page_body_lines — see its docstring for why a single header LENGTH
    can't be reused across pages of the same document. Defaults to
    is_start_page=True (the more conservative choice for a page examined in
    isolation, e.g. during marker discovery — see _read_page's docstring)."""
    header_lines, body_ordered = _read_page(page, is_start_page)
    return header_lines + body_ordered


def _read_page(page, is_start_page):
    """
    Returns (header_lines, body_ordered) for one page.

    Footer band (bottom BOTTOM_MARGIN_PT) is DROPPED entirely, universally —
    not repositioned, since a running footer is never real content by
    construction (repositioning it was tried and caused a real bug: a footer
    line parsed as a bogus entry with a nonsense page number).

    Top band (TOP_MARGIN_PT) is applied ONLY on the TOC's own START page —
    not on every 2-column page, and not on every page uniformly. Its only
    purpose is to isolate that one page's own preamble/title text (which can
    sit at an x-position that doesn't line up with the real columns, or can
    simply be a multi-line preamble that needs separating from the first real
    entry) — confirmed necessary twice, in opposite directions:
      - RP's "SOMMAIRE" (x0=380/y0=73, 2-column page) got routed into the
        RIGHT column bucket by x-position alone and pushed deep into the
        sequence — needed the top band to keep it out of column bucketing.
      - REG2A10/RP_CHOIX's own START pages (1-column) had their multi-line
        preamble ("RÈGLEMENT, TOME 2 ANNEXE X... Table des matières") glued
        onto the very first real entry once the top band was skipped for all
        1-column pages — needed the top band back, on the start page only.
    Applying it on CONTINUATION pages (1- or 2-column) is exactly what broke
    REG1_MS1.pdf: those pages' genuine content can start as early as y0≈70,
    well inside a naive top-margin cutoff, and 3 real entries
    (UG.1.4/UG.1.5/UG.1.6) were silently swallowed as "header" before this
    was scoped to the start page only.
    """
    lines_with_bbox = get_page_lines_with_bbox(page)
    if len(lines_with_bbox) < 6:
        ordered = [t for t, _, _ in sorted(lines_with_bbox, key=lambda r: r[2])]
        return [], ordered

    page_h = page.rect.height
    is_two_column, split_at = _decide_column_split(get_page_block_geometry(page), page.rect.width)
    top_margin = TOP_MARGIN_PT if is_start_page else 0

    if not is_two_column:
        blocks = get_page_lines_by_block(page)
        header_blocks = [b for b in blocks if b[1] < top_margin]
        body_blocks = [b for b in blocks if top_margin <= b[1] <= page_h - BOTTOM_MARGIN_PT]
        header_lines = [line for _, _, lines in sorted(header_blocks, key=lambda b: b[1]) for line in lines]
        body_blocks_sorted = sorted(body_blocks, key=lambda b: b[1])
        body_ordered = [line for _, _, lines in body_blocks_sorted for line in lines]
        return header_lines, body_ordered

    header_zone = sorted((r for r in lines_with_bbox if r[2] < top_margin), key=lambda r: r[2])
    header_lines = [t for t, _, _ in header_zone]

    blocks = get_page_lines_by_block(page)
    body_blocks = [b for b in blocks if top_margin <= b[1] <= page_h - BOTTOM_MARGIN_PT]

    left_blocks = sorted((b for b in body_blocks if b[0] <= split_at), key=lambda b: b[1])
    right_blocks = sorted((b for b in body_blocks if b[0] > split_at), key=lambda b: b[1])
    if not left_blocks or not right_blocks:
        body_blocks_sorted = sorted(body_blocks, key=lambda b: b[1])
        body_ordered = [line for _, _, lines in body_blocks_sorted for line in lines]
        return header_lines, body_ordered

    body_ordered = [line for _, _, lines in left_blocks for line in lines] + [
        line for _, _, lines in right_blocks for line in lines
    ]
    return header_lines, body_ordered


def get_page_body_lines(page, is_start_page):
    """
    Real content lines for one page: header/footer bands excluded (margin-
    band, page-specific AND start-vs-continuation-specific — see
    _read_page), with one more page-local fix: drop a LEADING line that is
    itself a TOC-title marker (handles a page whose title duplicates the
    running header's own text, e.g. REG1's start page).

    This replaces a single "header length" integer discovered once (from the
    TOC's first page) and reused for every page in the range — see
    _read_page's docstring for the concrete regressions that fixed.
    """
    _, body = _read_page(page, is_start_page)
    if body and is_toc_marker_line(body[0]):
        body = body[1:]
    return body


def page_is_two_column(page):
    is_two_column, _ = _decide_column_split(get_page_block_geometry(page), page.rect.width)
    return is_two_column


def discover_toc_start(doc):
    """Returns the physical page index of the TOC-title marker, or None.
    Each candidate is read AS IF it were the start page (is_start_page=True)
    — correct when it really is the start page; harmless when it isn't,
    since we're only scanning for the marker, not extracting content yet."""
    for i in range(min(15, doc.page_count)):
        header_lines, body_ordered = _read_page(doc[i], is_start_page=True)
        candidate_lines = (header_lines + body_ordered)[:10]
        for l in candidate_lines:
            if is_toc_marker_line(l):
                return i
    return None


TOC_SHAPE_DENSITY_THRESHOLD = 0.5


def discover_toc_shape_start(doc):
    """Marker-INDEPENDENT fallback: returns the physical page index of the
    first page whose own lines are DENSELY TOC-shaped (dot-leader + own
    trailing page number — see looks_like_dense_toc_line), or None. Only
    called when discover_toc_start (the marker search) already failed —
    this is an ADDITIONAL signal, not a replacement, so a document with a
    real "sommaire"/"table des matières" marker is always found by the
    (unchanged) marker path first and never reaches this function at all.

    Confirmed necessary on RP_20251017_MC1_HOTEL_DIEU.pdf: its real TOC
    ("1. Préambule ... 3", "1.1. Rappel du projet ... 3", ...) never
    contains either marker word anywhere in the document, so
    discover_toc_start always returns None for it — yet its TOC page has a
    94% dot-leader-shaped-line density (30/32), a real content page has 0%
    (checked across RP_20251017_MC1_HOTEL_DIEU's and RP_PREAMBULE.pdf's
    first 15 pages), so a 50% floor has wide margin on both sides and won't
    mistake an ordinary page with "a couple of numbers" for a sommaire.
    """
    for i in range(min(15, doc.page_count)):
        header_lines, body_ordered = _read_page(doc[i], is_start_page=True)
        lines = header_lines + body_ordered
        if not lines:
            continue
        shaped = sum(1 for l in lines if looks_like_dense_toc_line(l))
        if shaped / len(lines) >= TOC_SHAPE_DENSITY_THRESHOLD:
            return i
    return None


def detect_toc_pages(doc, toc_start):
    toc_pages = [toc_start]
    i = toc_start + 1
    while i < doc.page_count:
        body = get_page_body_lines(doc[i], is_start_page=False)
        if not body:
            break
        ratio = sum(1 for l in body if looks_like_toc_line(l)) / len(body)
        if ratio < 0.3:
            break
        toc_pages.append(i)
        i += 1
    return toc_pages


def split_fused_pagenum_and_next_numbering(lines):
    out = []
    for line in lines:
        m = RE_FUSED_PAGENUM_AND_NEXT_NUMBERING.match(line)
        if m:
            out.append(m.group(1))
            out.append(m.group(2))
        else:
            out.append(line)
    return out


def merge_isolated_numbering_lines(lines):
    out = []
    i = 0
    while i < len(lines):
        if RE_ISOLATED_NUMBERING.match(lines[i]) and i + 1 < len(lines):
            out.append(f"{lines[i]} {lines[i + 1]}")
            i += 2
        else:
            out.append(lines[i])
            i += 1
    return out


# ---------------------------------------------------------------------------
# Shared: grouping, tree build, page_fin, anchor check.
# ---------------------------------------------------------------------------
class Entry:
    def __init__(self, numero, titre, niveau, page_debut):
        self.numero = numero
        self.titre = titre
        self.niveau = niveau
        self.page_debut = page_debut
        self.page_fin = None
        self.nb_pages = None
        self.enfants = []


def group_raw_lines(raw_lines, warnings):
    def starts_uppercase(line: str) -> bool:
        letters = [c for c in line if c.isalpha()]
        return bool(letters) and letters[0].isupper()

    def has_trailing_page_number(line: str) -> bool:
        text = re.sub(r"\.{2,}", " ", line).strip()
        return bool(re.search(r"\d\s*$", text))

    def is_genuine_close(line: str) -> bool:
        """True if `line` has a trailing page number AND carries some real
        (non-digit) content of its own beyond just that number — a
        complete, self-evident TOC row (e.g. "...Evolution du Plan C ...
        30" or "...effets du PLU ... 20"), as opposed to a BARE,
        content-free digit-only line that merely happens to end in a digit.
        Used only to decide whether a CLOSE was "strong" enough to make a
        later bare-digit-only line into pure noise (see is_signal_free_
        noise's docstring) — NOT used for the has_trailing_page_number-
        based closing decision itself, which must stay exactly as before:
        RP_EVAL.pdf's "Bilan des effets ... du PLU" / "342" / "Mesures
        d'accompagnement..." needs "342" alone to close the entry
        immediately (has_trailing_page_number's existing behavior), so that
        the next, unnumbered, uppercase-starting entry is still recognized
        as new — requiring real content on "342" itself here would leave
        the entry open and wrongly fuse the next one into it."""
        if not has_trailing_page_number(line):
            return False
        stripped = re.sub(r"\.(?:\s?\.){2,}", " ", line).strip()
        return any(c.isalpha() for c in stripped)

    def is_signal_free_noise(line: str) -> bool:
        """True if `line` has NO alphabetic content at all once dot-leaders
        are stripped — bare digits/punctuation with no title text
        whatsoever (confirmed on RP_20251017_MC1_HOTEL_DIEU.pdf: a lone
        stray "2" sitting between two real TOC rows, a PDF rendering
        artifact, not real content). A line like this can never
        legitimately be either a new entry (no title text to give it one)
        or genuine closing text of a still-open wrap (a real closing line
        always carries actual prose too, e.g. "...effets du PLU ... 20") —
        it is pure noise and must be dropped, never silently absorbed into
        whatever group happens to be open. Only checked once the current
        group is already CLOSED: a still-open wrap's own intermediate lines
        are left exactly as before (untouched, no new behavior introduced
        there), since no bug was observed or reported for that branch and
        the fix should stay scoped to what's actually broken."""
        stripped = re.sub(r"\.(?:\s?\.){2,}", " ", line)
        return not any(c.isalpha() for c in stripped)

    def has_unclosed_bracket(lines: list[str]) -> bool:
        """True if the group accumulated so far leaves a '[' still open.

        A TOC title may carry a bracketed source reference ("[Annexe X et
        atlas n° 2 du règlement]", "[Règlement, sous-section UG.1.5.1 et fond
        de plan de l'Atlas n°2]") that WRAPS across several rendered lines.
        When it does, the continuation line can legitimately begin with a
        token that parse_prefix recognises as numbering — confirmed on
        Rapport_presentation_MS1.pdf, whose entry 2.9 wraps as:

            "2.9. Application de la règle de mixité sociale (UG.1.5.1) à la Cité"
            "internationale universitaire de Paris [Règlement, sous-section"
            "UG.1.5.1 et fond de plan de l'Atlas n°2] .... 79"

        The third line opens with "UG.1.5.1", which RE_CODE_DOTGROUPS matches,
        so the still-open group was split there: the fragment became a bogus
        standalone entry ("UG.1.5.1 — et fond de plan de l'Atlas n°2]", p.79)
        AND real section 2.9 — left with no page number of its own — was then
        dropped outright by parse_groups. One wrapped bracket, two failures.

        An unclosed '[' is a reliable, shape-based "we are still inside this
        title" signal: a genuine new TOC entry never begins while the previous
        entry has a bracket dangling open, because a title's own brackets are
        always balanced by the time that title ends. Used ONLY to veto the
        not-yet-closed branch's prefix test, so the still-open vs
        already-closed distinction from the prior fixes is untouched: a group
        that has already met its page number closes exactly as before, and an
        entry that genuinely has no page number still yields to the next
        numbered entry exactly as before (unless a bracket is open, which is
        precisely the case that was broken).
        """
        depth = 0
        for line in lines:
            for ch in line:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth = max(0, depth - 1)
        return depth > 0

    def looks_like_standalone_toc_entry(line: str) -> bool:
        """A dot-leader run (the classic TOC ellipsis filler to a page
        number — either consecutive dots "...." or, as rendered in this
        document, individually SPACED dots ". . . ." — a dot followed by
        2+ repeats of an optional space then another dot) ending in the
        line's OWN trailing page number is a strong, shape-based signal
        that this line is a complete entry in its own right, regardless of
        what it starts with. Needed because `starts_uppercase` only
        recognizes a leading LETTER — a title that opens with a statistic
        ("18,1% de logements inoccupés à Paris ... 82") has no leading
        letter at all, so without this it got silently swallowed into the
        PRECEDING, already-closed entry (confirmed on RP_DIAGNOSTIC.pdf;
        the line sits in the exact same block/column as its neighbors, so
        this was never a position/column issue). Only checked once the
        current group is already CLOSED: a still-open multi-line wrap's own
        closing line legitimately has this same dot-leader-plus-page-number
        shape (e.g. RP_RNT.pdf's "...effets du PLU ........... 20"), so
        this must never gate the not-yet-closed branch or it would re-break
        that join."""
        return bool(re.search(r"\.(?:\s?\.){2,}", line)) and has_trailing_page_number(line)

    groups = []
    current: list[str] = []
    current_closed = False
    current_closed_strong = False
    for line in raw_lines:
        if current:
            if current_closed:
                starts_new = (
                    parse_prefix(line) is not None
                    or starts_uppercase(line)
                    or looks_like_standalone_toc_entry(line)
                )
            else:
                starts_new = parse_prefix(line) is not None and not has_unclosed_bracket(current)
        else:
            starts_new = False

        # Only a STRONG close (the closing line itself had real content, not
        # just a bare digit) makes a later bare-digit-only line into pure
        # noise. A WEAK close (current_closed but NOT current_closed_strong
        # — the group only looks closed because some earlier line happened
        # to end in a digit with no other content, e.g. RP_INDIC.pdf's
        # "2023") must keep absorbing what comes next exactly as before:
        # the real page number ("27") still needs to be appended so
        # clean_title_and_extract_page can correctly pick IT as the
        # trailing digit, not the coincidental "2023".
        if not starts_new and current and current_closed_strong and is_signal_free_noise(line):
            warnings.append(
                f"ligne sans aucun signal d'entrée (ni numérotation, ni majuscule, ni "
                f"points de suite, ni texte) ignorée après une entrée déjà close : {line!r}"
            )
            continue

        if starts_new:
            groups.append(current)
            current = [line]
            current_closed = has_trailing_page_number(line)
            current_closed_strong = is_genuine_close(line)
        else:
            current.append(line)
            if has_trailing_page_number(line):
                current_closed = True
                if is_genuine_close(line):
                    current_closed_strong = True
    if current:
        groups.append(current)
    return groups


def parse_groups(groups, warnings):
    parsed: list[ParsedLine] = []
    for group in groups:
        joined = " ".join(group)
        prefix = parse_prefix(group[0])
        if prefix:
            numbering_type, category, raw_numbering, depth, remainder_first_line = prefix
            text_for_title = " ".join([remainder_first_line] + group[1:])
        else:
            all_caps = is_all_caps_header(group[0])
            numbering_type, category, raw_numbering, depth, _ = classify_group_first_line(group[0], all_caps)
            text_for_title = joined

        title, page = clean_title_and_extract_page(text_for_title)
        if page is None:
            warnings.append(f"Entry with no trailing page number found, skipped: {joined!r}")
            continue
        if not title:
            title = "(untitled)"

        if raw_numbering is None:
            warnings.append(
                f"Entry with no numbering code (numero=None), kept but flagged "
                f"(convention: fused into parent, not standalone): titre={title!r} page_debut={page}"
            )

        parsed.append(
            ParsedLine(
                numbering_type=numbering_type,
                category=category,
                raw_numbering=raw_numbering,
                depth=depth,
                titre=title,
                page_debut=page,
            )
        )
    return parsed


def build_tree(parsed: list[ParsedLine], levels: list[int]):
    root_children = []
    stack = []
    flat_order = []
    for pe, level in zip(parsed, levels):
        entry = Entry(pe.raw_numbering, pe.titre, level, pe.page_debut)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].enfants.append(entry)
        else:
            root_children.append(entry)
        stack.append((level, entry))
        flat_order.append(entry)
    return root_children, flat_order


def compute_page_fin(flat_order, n_pages, warnings):
    for idx, entry in enumerate(flat_order):
        next_boundary = None
        for later in flat_order[idx + 1 :]:
            if later.niveau <= entry.niveau:
                next_boundary = later
                break
        if next_boundary is None:
            entry.page_fin = n_pages
            entry.nb_pages = entry.page_fin - entry.page_debut + 1
            warnings.append(
                f"Last entry of its branch, page_fin not reliably derivable — set to "
                f"doc.page_count ({n_pages}): numero={entry.numero!r} titre={entry.titre!r} "
                f"page_debut={entry.page_debut}"
            )
            continue
        raw_page_fin = next_boundary.page_debut - 1
        if raw_page_fin < entry.page_debut:
            entry.page_fin = entry.page_debut
            entry.nb_pages = 1
            warnings.append(
                f"Entry shares its start page with the next entry (dense page, "
                f"page-level extent not resolvable): numero={entry.numero!r} "
                f"titre={entry.titre!r} page_debut={entry.page_debut}, "
                f"next entry starts on page {next_boundary.page_debut} "
                f"(numero={next_boundary.numero!r}). page_fin/nb_pages set to a "
                f"1-page lower bound."
            )
        else:
            entry.page_fin = raw_page_fin
            entry.nb_pages = entry.page_fin - entry.page_debut + 1


def validate_levels(flat_order, warnings):
    """Guard net, not a fix: niveau < 1 is structurally impossible (the first
    entry of any document is always level 1, see compute_relative_levels) —
    if the relative-level engine ever produces one anyway (e.g. a NESTING
    entry whose depth-delta is computed against an unrelated entry because
    reading order was wrong), ship the entry as-is but flag it loudly rather
    than silently passing off a broken tree as clean."""
    for entry in flat_order:
        if entry.niveau < 1:
            warnings.append(
                f"NIVEAU INVALIDE (< 1), entrée conservée telle quelle mais probablement mal "
                f"placée dans l'arbre : numero={entry.numero!r} titre={entry.titre!r} "
                f"page_debut={entry.page_debut} niveau={entry.niveau}"
            )


def detect_duplicate_codes(root_children, warnings):
    """Flags (never auto-corrects) a numbering code that appears twice among
    the DIRECT children of the same parent — a genuine error in the source
    PLU document (e.g. RP_RNT.pdf's section 3 restarting at "3.1" instead of
    continuing to "3.6"), not an extraction bug. Both entries are kept as
    distinct tree nodes; this only records the collision so it isn't
    silently lost, and calls out that any code-derived key downstream (e.g.
    a lookup keyed by numero) will collide and needs disambiguating at
    injection time. Human call, not ours: do not renumber or merge."""

    def walk(siblings, parent_label):
        by_code: dict[str, list[Entry]] = {}
        for e in siblings:
            if e.numero is not None:
                by_code.setdefault(e.numero, []).append(e)
        for code, entries in by_code.items():
            if len(entries) > 1:
                pages = " et ".join(f"p.{e.page_debut}" for e in entries)
                warnings.append(
                    f"code dupliqué dans le document source : '{code}' apparaît {len(entries)}x "
                    f"sous {parent_label} ({pages}) — conservé tel quel, non renuméroté (décision "
                    f"humaine à prendre) ; toute clé dérivée du code '{code}' sous {parent_label} "
                    f"entrera en collision, à désambiguïser à l'injection"
                )
        for e in siblings:
            child_label = f"la section {e.numero}" if e.numero else f"« {e.titre} »"
            walk(e.enfants, child_label)

    walk(root_children, "la racine du document")


def detect_sequence_gaps(root_children, warnings):
    """Flags (never invents/renumbers) a missing integer among the DIRECT
    children of the same parent when their numero values are BARE integers
    (e.g. root-level sections "1", "2", "4" with no "3" —
    RP_20251017_MC1_HOTEL_DIEU.pdf) — a genuine gap in the source document,
    not an extraction bug. Deliberately narrow: only bare-integer numero
    values are checked, since "the next expected value" is unambiguous only
    for that shape; a dotted code (UGSU.2.1, Annexe II, PARTIE 3, Tableau N)
    has no well-defined "next expected sub-code" and is left alone."""

    def walk(siblings, parent_label):
        integers = sorted({int(e.numero) for e in siblings if e.numero is not None and re.fullmatch(r"\d+", e.numero)})
        for a, b in zip(integers, integers[1:]):
            if b - a > 1:
                missing = ", ".join(str(x) for x in range(a + 1, b))
                warnings.append(f"trou de séquence : section {missing} absente sous {parent_label} ({a} → {b})")
        for e in siblings:
            child_label = f"la section {e.numero}" if e.numero else f"« {e.titre} »"
            walk(e.enfants, child_label)

    walk(root_children, "la racine")


def anchor_check(doc, flat_order, n_pages):
    sample_n = min(10, len(flat_order))
    if sample_n == 0:
        return dict(sample_size=0, match_rate=None, dominant_offset_detected=None, offset_votes={}, results=[]), []
    step = len(flat_order) / sample_n
    sample_indices = sorted({int(i * step) for i in range(sample_n)})

    results = []
    offset_votes: dict[int, int] = {}
    warnings = []
    for idx in sample_indices:
        entry = flat_order[idx]
        naive_phys = entry.page_debut - 1
        title_key = normalize(entry.titre)
        found_offset = None
        for delta in range(-3, 4):
            phys = naive_phys + delta
            if phys < 0 or phys >= n_pages:
                continue
            page_text = normalize(doc[phys].get_text())
            if title_key and title_key in page_text:
                found_offset = delta
                break
        matched = found_offset is not None
        results.append(dict(numero=entry.numero, titre=entry.titre, page_debut=entry.page_debut,
                             naive_physical_index=naive_phys, matched=matched, offset_detected=found_offset))
        if matched:
            offset_votes[found_offset] = offset_votes.get(found_offset, 0) + 1
        else:
            warnings.append(
                f"Anchor check MISMATCH: numero={entry.numero!r} titre={entry.titre!r} "
                f"page_debut={entry.page_debut} — title not found within +/-3 pages of naive physical index {naive_phys}"
            )
    n_matched = sum(1 for r in results if r["matched"])
    match_rate = n_matched / len(results) if results else None
    dominant_offset = max(offset_votes, key=offset_votes.get) if offset_votes else None
    return dict(sample_size=len(results), match_rate=match_rate, dominant_offset_detected=dominant_offset,
                offset_votes=offset_votes, results=results), warnings


def entry_to_dict(e: Entry):
    return dict(numero=e.numero, titre=e.titre, niveau=e.niveau, page_debut=e.page_debut,
                page_fin=e.page_fin, nb_pages=e.nb_pages, enfants=[entry_to_dict(c) for c in e.enfants])


NUMBERING_RULE_USED = (
    "Numbering profiles tried in order: PARTIE n (FLAT), Annexe <roman> (FLAT), "
    "<ordinal> partie (FLAT), Axe n (FLAT), a leading code + N dot-digit-groups e.g. "
    "UG.1.4/C.1/N.1.2.2 (NESTING, depth=N), a bare digit-dot chain e.g. 1/1.1/2.14 "
    "(NESTING, depth=dot-group count), a single letter or roman numeral (FLAT), an "
    "ALL-CAPS unnumbered heading (FLAT). LEVEL is relative, not absolute depth: a "
    "recurring FLAT profile jumps back to its first-occurrence level regardless of "
    "what happened in between; a NESTING profile moves by the signed depth-delta vs. "
    "its own last occurrence; an unnumbered/non-caps line inherits the previous "
    "level; first entry of the document = level 1. Header length and TOC-page range "
    "are discovered per document (title-marker search + shape-based continuation), "
    "not assumed from a fixed line count. TOC pages are read in column order "
    "(block-level x0 clustering decides 1 vs 2 columns; fragments sharing a block "
    "and a y-band are coalesced before grouping, to avoid a wide sentence or a "
    "mid-sentence number corrupting the read)."
)


def process(pdf_path: Path, out_path: Path | None):
    doc = pymupdf.open(str(pdf_path))
    n_pages = doc.page_count
    warnings: list[str] = []

    toc_start = discover_toc_start(doc)
    if toc_start is None:
        toc_start = discover_toc_shape_start(doc)
        if toc_start is not None:
            warnings.append(
                f"sommaire détecté sans marqueur \"sommaire\"/\"table des matières\" — "
                f"repéré par densité de lignes en forme de table des matières (page {toc_start + 1})"
            )
    if toc_start is None:
        warnings.append("aucun sommaire détecté dans ce document")
        output = dict(
            doc=pdf_path.name,
            chemin_relatif=pdf_path.relative_to(REPO_ROOT).as_posix(),
            sommaire_pages_detected=dict(physical_indices_0based=[], printed_page_numbers=[]),
            numbering_rule_used="n/a — aucun sommaire détecté",
            anchor_check=dict(sample_size=0, match_rate=None, dominant_offset_detected=None, offset_votes={}, results=[]),
            warnings=warnings,
            arbre=[],
        )
        if out_path is not None:
            out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        return output, [], doc, []

    toc_pages = detect_toc_pages(doc, toc_start)
    col_report = [2 if page_is_two_column(doc[i]) else 1 for i in toc_pages]

    raw_lines = []
    for i in toc_pages:
        raw_lines.extend(get_page_body_lines(doc[i], is_start_page=(i == toc_start)))
    raw_lines = [l for l in raw_lines if not is_boilerplate_line(l)]
    raw_lines = split_fused_pagenum_and_next_numbering(raw_lines)
    raw_lines = merge_isolated_numbering_lines(raw_lines)

    groups = group_raw_lines(raw_lines, warnings)
    parsed = parse_groups(groups, warnings)
    levels = compute_relative_levels(parsed)
    root_children, flat_order = build_tree(parsed, levels)
    validate_levels(flat_order, warnings)
    detect_duplicate_codes(root_children, warnings)
    detect_sequence_gaps(root_children, warnings)
    compute_page_fin(flat_order, n_pages, warnings)
    anchor, anchor_warnings = anchor_check(doc, flat_order, n_pages)
    warnings.extend(anchor_warnings)

    output = dict(
        doc=pdf_path.name,
        chemin_relatif=pdf_path.relative_to(REPO_ROOT).as_posix(),
        sommaire_pages_detected=dict(physical_indices_0based=toc_pages, printed_page_numbers=[p + 1 for p in toc_pages]),
        numbering_rule_used=NUMBERING_RULE_USED,
        anchor_check=anchor,
        warnings=warnings,
        arbre=[entry_to_dict(e) for e in root_children],
    )
    if out_path is not None:
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, flat_order, doc, col_report


def tree_level_summary(flat_order):
    counts: dict[int, int] = {}
    for e in flat_order:
        counts[e.niveau] = counts.get(e.niveau, 0) + 1
    return counts
