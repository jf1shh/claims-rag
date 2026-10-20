"""Shared ingestion bounds, used by the HTTP and queue paths."""
from pathlib import Path
from zipfile import ZipFile, is_zipfile

# Shared by the sync and async upload paths, and by source_storage.stage_source,
# so a staged file's declared content type always matches its extension.
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "txt": "text/plain",
}


class DocumentLimitError(ValueError):
    pass


def check_container(path, max_bytes, max_chars):
    if Path(path).stat().st_size > max_bytes:
        raise DocumentLimitError('Document exceeds byte limit.')
    if is_zipfile(path):
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000:
                raise DocumentLimitError('Document archive exceeds expanded size limit.')
            total_size = 0
            text_size = 0
            for info in entries:
                total_size += info.file_size
                name = info.filename.replace('\\', '/').lower()
                # Media (images/embedded binaries, already compressed) is excluded
                # from the text-expansion cap so a photo-heavy DOCX isn't rejected;
                # it still counts toward the overall zip-bomb cap below.
                if name.endswith(('.xml', '.rels', '.txt')) or '/media/' not in name:
                    text_size += info.file_size
            if text_size > max_chars * 4 or total_size > max_bytes * 10:
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
        # Minimal environment: the parser subprocess has no business seeing
        # API keys, credentials, or any other secret the parent process holds.
        passthrough = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'PYTHONPATH',
                        'VIRTUAL_ENV', 'SYSTEMROOT')
        env = {name: os.environ[name] for name in passthrough if name in os.environ}
        env['OPENBLAS_NUM_THREADS'] = '1'
        env['OMP_NUM_THREADS'] = '1'
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'backend.parse_document', str(Path(path).resolve()),
                 kind, str(output), str(max_chars)],
                cwd=REPO_ROOT, timeout=timeout, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentLimitError('Document parsing exceeded its time limit.') from exc
        if result.returncode == 3:
            raise DocumentLimitError('Document exceeds extracted text limit.')
        if result.returncode != 0:
            raise ValueError('Document could not be parsed within resource limits.')
        return check_text(output.read_text(encoding='utf-8'), max_chars)
