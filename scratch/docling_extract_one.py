"""Single-file Docling extraction worker, run as its own subprocess (by
scratch/ingest_families_docling.py) so a hang or crash on one PDF can be
killed by a wall-clock timeout without taking the rest of a batch down with
it -- same converter options as scratch/extract_reg1_content.py (do_ocr
off: born-digital assumption, unverified per-file; do_table_structure on).

Usage: python3 scratch/docling_extract_one.py <pdf_path> <out_json_path>
Exit 0 + writes out_json_path (doc.export_to_dict()) on success.
Exit 1 + prints the exception to stderr on failure (caller decides how to
log/report it -- this script only extracts, never judges fit).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


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
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    converter = build_converter()
    t0 = time.time()
    result = converter.convert(str(pdf_path))
    doc = result.document
    dt = time.time() - t0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc.export_to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    n_headers = sum(1 for item, _ in doc.iterate_items() if item.label.value == "section_header")
    n_tables = sum(1 for item, _ in doc.iterate_items() if item.label.value == "table")
    print(f"OK {pdf_path.name} ({dt:.1f}s, {result.input.page_count}p, status={result.status}, "
          f"{n_headers} section_header(s), {n_tables} table(s))")


if __name__ == "__main__":
    main()
