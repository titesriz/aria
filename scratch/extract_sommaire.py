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
RE_CODE_DOTGROUPS = re.compile(r"^([A-Z]{1,10}(?:\.\d+)+)\.?\s+(.*)$")  # UG.1.4, C.1, N.1.2.2
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
    m = RE_CODE_DOTGROUPS.match(line)
    if m:
        raw = m.group(1)
        depth = sum(1 for p in raw.split(".")[1:] if p.isdigit())
        return ("DOTCHAIN", "NESTING", raw, depth, m.group(2))
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


def get_page_block_x0s(page):
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        text = "".join(s.get("text", "") for line in block.get("lines", []) for s in line.get("spans", []))
        if text.strip():
            out.append(block["bbox"][0])
    return out


def _decide_column_split(block_x0s, page_width):
    """1-vs-2-column decision from BLOCK-level x0 (single source of truth,
    used both by real reordering and the console report). 2 columns only
    when the largest x0 gap is wide (>15% of page width), lands away from the
    edges (25%-75% of page width), AND both sides carry a substantial,
    comparable share of the page's blocks (>=20%, floor 5) — distinguishes a
    real 2-column body from decorative header/footer text on one side.
    Landscape orientation is a WEAK hint only — decided purely by this
    geometry, never by page rotation/aspect. Returns (is_two_column, split_at)."""
    if len(block_x0s) < 6:
        return False, None

    xs = sorted(set(round(x0) for x0 in block_x0s))
    best_gap, split_at = 0, None
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        if gap > best_gap:
            best_gap, split_at = gap, (a + b) / 2

    gap_looks_two_column = (
        split_at is not None
        and best_gap > 0.15 * page_width
        and 0.25 * page_width < split_at < 0.75 * page_width
    )
    if not gap_looks_two_column:
        return False, None

    left_n = sum(1 for x0 in block_x0s if x0 <= split_at)
    right_n = len(block_x0s) - left_n
    min_side = max(5, int(0.2 * len(block_x0s)))
    if left_n < min_side or right_n < min_side:
        return False, None

    return True, split_at


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
    is_two_column, split_at = _decide_column_split(get_page_block_x0s(page), page.rect.width)
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
    is_two_column, _ = _decide_column_split(get_page_block_x0s(page), page.rect.width)
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


def group_raw_lines(raw_lines):
    def starts_uppercase(line: str) -> bool:
        letters = [c for c in line if c.isalpha()]
        return bool(letters) and letters[0].isupper()

    def has_trailing_page_number(line: str) -> bool:
        text = re.sub(r"\.{2,}", " ", line).strip()
        return bool(re.search(r"\d\s*$", text))

    groups = []
    current: list[str] = []
    current_closed = False
    for line in raw_lines:
        if current:
            if current_closed:
                starts_new = parse_prefix(line) is not None or starts_uppercase(line)
            else:
                starts_new = parse_prefix(line) is not None
        else:
            starts_new = False

        if starts_new:
            groups.append(current)
            current = [line]
            current_closed = has_trailing_page_number(line)
        else:
            current.append(line)
            if has_trailing_page_number(line):
                current_closed = True
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
        warnings.append("aucun sommaire détecté dans ce document")
        output = dict(
            doc=pdf_path.name,
            chemin_relatif=str(pdf_path.relative_to(REPO_ROOT)),
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

    groups = group_raw_lines(raw_lines)
    parsed = parse_groups(groups, warnings)
    levels = compute_relative_levels(parsed)
    root_children, flat_order = build_tree(parsed, levels)
    compute_page_fin(flat_order, n_pages, warnings)
    anchor, anchor_warnings = anchor_check(doc, flat_order, n_pages)
    warnings.extend(anchor_warnings)

    output = dict(
        doc=pdf_path.name,
        chemin_relatif=str(pdf_path.relative_to(REPO_ROOT)),
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
