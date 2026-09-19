"""Full-content Docling extraction for REG1_MS1.pdf (règlement écrit, Tome 1,
250 pages, family=reglement_ecrit, validity=current per corpus_mapping.yaml
-- see corpus_mapping.yaml lines 49/82) -- same pipeline as
extract_addenda_content.py (do_ocr off: born-digital; do_table_structure on:
the règlement's dimensional tables carry real content).

Docling version: 2.128.0 (pinned, matches extract_addenda_content.py /
extract_titles.py).

Output: scratch/reg1_content.md, scratch/reg1_content.json (export_to_dict()).

Run: python3 scratch/extract_reg1_content.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = REPO_ROOT / "Ressources" / "PLU bioclimatique" / "Règlement" / "Pièces écrites" / "Tome 1" / "REG1_MS1.pdf"
OUT_DIR = Path(__file__).resolve().parent


def build_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.do_picture_classification = False
    opts.do_picture_description = False
    opts.do_chart_extraction = False
    opts.do_code_enrichment = False
    opts.do_formula_enrichment = False
    opts.generate_page_images = False
    opts.generate_picture_images = False
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def main():
    assert PDF_PATH.exists(), PDF_PATH
    converter = build_converter()
    t0 = time.time()
    result = converter.convert(str(PDF_PATH))
    doc = result.document
    dt = time.time() - t0

    md = doc.export_to_markdown()
    (OUT_DIR / "reg1_content.md").write_text(md, encoding="utf-8")
    (OUT_DIR / "reg1_content.json").write_text(
        json.dumps(doc.export_to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    n_tables = sum(1 for item, _ in doc.iterate_items() if item.label.value == "table")
    n_headers = sum(1 for item, _ in doc.iterate_items() if item.label.value == "section_header")
    print(f"REG1_MS1.pdf ({dt:.1f}s, {result.input.page_count}p, status={result.status}) "
          f"-> {len(md)} md chars, {n_tables} table(s), {n_headers} section_header(s)")


if __name__ == "__main__":
    main()
