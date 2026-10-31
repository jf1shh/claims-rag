---
title: "Docling OCR fallback: torchvision ABI crash was a Python-version bug, not a missing-wheel bug"
author: Jared
date: 2026-10-31
branch: worktree-docling-ocr-fallback
cost: { minutes: 9, tokens: 185261 }
touches:
  - backend/api.py
  - backend/document_limits.py
  - backend/ingestion_worker.py
  - config.py
  - backend/ocr_fallback_worker.py
  - scripts/setup_docling_ocr.sh
  - tests/test_ocr_fallback.py
  - ".handoff/loop-977922/HANDOFF.md"
---

## Decided
- The torchvision::nms ABI crash from installing Docling in a CPU-only scratch venv was caused
  by the venv defaulting to whatever `python3` resolves to on this machine (3.14, CachyOS rolling
  release), not by a missing CPU wheel. Building the venv with `uv venv --python 3.13` (matching
  the main project venv) and installing torch+torchvision together in ONE
  `uv pip install --python <venv>/bin/python torch torchvision --index-url .../whl/cpu` call
  fixed it cleanly on the first retry -- both landed as matched `+cpu` builds and Docling OCR
  then worked correctly against a synthetic scanned PDF (claim #, VIN, all dollar amounts, total).

## Tried and ruled out
- Plain `pip install torchvision==X --index-url ...` after torch was already installed: pip
  reports "Requirement already satisfied" and does NOT re-resolve to a better-matching build even
  when pointed at a different index -- it only checks whether the installed version string
  satisfies the specifier, not whether a build-tag-matched wheel exists elsewhere. This is what
  produced the mismatched torch+cpu / torchvision-plain-PyPI pairing that crashed.
- Did not chase Docling's OCR-only/"lite" pipeline mode (skip layout-detection/torchvision
  entirely) as an alternative fix -- unnecessary once the real root cause (wrong Python version)
  was found and fixed in one retry.

## Watch out
- On this machine, bare `python3`/`python3 -m venv` is NOT the same version as the project's own
  `.venv` (3.14 vs 3.13). Any throwaway/scratch venv for a project dependency should explicitly
  pin `uv venv --python 3.13` (or whatever the project's own .venv reports via
  `.venv/bin/python --version`) rather than trusting the bare `python3` on PATH.
- When pairing two interdependent packages from a non-default index (e.g. CPU-only
  torch+torchvision), always install both in the SAME pip/uv invocation. Installing them
  separately risks a silent mismatched pair that only fails at import/runtime, not at install time.
