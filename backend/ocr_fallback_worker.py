"""Isolated OCR-fallback entry point for a scanned (no-text-layer) PDF.

Runs under a SEPARATE, operator-provisioned Docling venv (scripts/setup_docling_ocr.sh),
never the main project venv/interpreter -- Docling's torch/torchvision stack must never
touch the pinned ROCm torch build (see PR #67). No imports from the main backend package:
this module has to import cleanly in a venv that only has Docling installed, and the main
venv's interpreter never runs it.
"""
import html
from pathlib import Path
import sys


def main():
    path, output, max_chars = sys.argv[1:]
    try:
        from docling.document_converter import DocumentConverter
        text = html.unescape(DocumentConverter().convert(path).document.export_to_markdown())
    except Exception:
        return 2
    if len(text) > int(max_chars):
        return 3
    Path(output).write_text(text, encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
