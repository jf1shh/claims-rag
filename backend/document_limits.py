"""Shared ingestion bounds, used by the HTTP and queue paths."""
from pathlib import Path
from zipfile import ZipFile, is_zipfile


class DocumentLimitError(ValueError):
    pass


def check_container(path, max_bytes, max_chars):
    if Path(path).stat().st_size > max_bytes:
        raise DocumentLimitError('Document exceeds byte limit.')
    if is_zipfile(path):
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(i.file_size for i in entries) > max_chars * 4:
                raise DocumentLimitError('Document archive exceeds expanded size limit.')


def check_text(text, max_chars):
    if len(text) > max_chars:
        raise DocumentLimitError('Document exceeds extracted text limit.')
    return text


def parse_bounded(path, kind, max_bytes, max_chars, timeout):
    import os
    import subprocess
    import sys
    import tempfile
    from config import REPO_ROOT
    check_container(path, max_bytes, max_chars)
    if kind == 'txt':
        # Plain text needs only bounded decoding, not a complex file parser.
        with Path(path).open(encoding='utf-8', errors='ignore') as source:
            return check_text(source.read(max_chars + 1), max_chars)
    with tempfile.TemporaryDirectory(prefix='claims-parse-') as folder:
        output = Path(folder) / 'text.txt'
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'backend.parse_document', str(Path(path).resolve()),
                 kind, str(output), str(max_chars)],
                cwd=REPO_ROOT, timeout=timeout, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'},
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentLimitError('Document parsing exceeded its time limit.') from exc
        if result.returncode == 3:
            raise DocumentLimitError('Document exceeds extracted text limit.')
        if result.returncode != 0:
            raise ValueError('Document could not be parsed within resource limits.')
        return check_text(output.read_text(encoding='utf-8'), max_chars)
