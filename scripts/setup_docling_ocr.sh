#!/usr/bin/env bash
# Provisions a SEPARATE venv for Docling's OCR fallback (scanned-PDF text extraction).
#
# This never touches the project's own .venv: Docling pulls a ~6GB CPU torch/torchvision/
# transformers stack that would force-upgrade the pinned ROCm torch build this project
# relies on (see PR #67), so it stays out of requirements.txt and out of CI entirely.
#
# torch and torchvision MUST be installed from the SAME --index-url call. Installing them
# separately (or letting docling pull its own) lets pip "already satisfied" its way into a
# torchvision build with no matching local tag for the torch build actually installed --
# that mismatch fails at import time with "operator torchvision::nms does not exist".
set -euo pipefail

VENV_DIR="${1:-.venv-docling}"
PYTHON_BIN="${PYTHON_BIN:-python3.13}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "error: $PYTHON_BIN not found. Docling's CPU-tagged torchvision wheel was only" >&2
    echo "confirmed to exist for Python 3.13 (matching this project's main venv) -- a" >&2
    echo "different Python 3.x may simply have no matching wheel on the CPU index." >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    uv venv --python "$PYTHON_BIN" "$VENV_DIR"
    INSTALL=(uv pip install --python "$VENV_DIR/bin/python")
else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/python" -m ensurepip --upgrade
    INSTALL=("$VENV_DIR/bin/pip" install)
fi

"${INSTALL[@]}" torch torchvision --index-url https://download.pytorch.org/whl/cpu
"${INSTALL[@]}" docling

"$VENV_DIR/bin/python" -c "
import torch, torchvision
assert torch.__version__.endswith('+cpu'), torch.__version__
assert torchvision.__version__.endswith('+cpu'), torchvision.__version__
print('torch', torch.__version__)
print('torchvision', torchvision.__version__)
"

echo
echo "Docling OCR venv ready at: $VENV_DIR"
echo "Set this in .env to enable the fallback:"
echo "  OCR_FALLBACK_PYTHON=$(cd "$VENV_DIR" && pwd)/bin/python"
