"""Shared ingestion bounds, used by the HTTP and queue paths."""
import struct
from pathlib import Path
from zipfile import ZipFile, is_zipfile

# End-of-central-directory record (fixed part, 22 bytes) and its ZIP64
# counterparts. A zip's declared entry count lives in this record, which sits
# at the end of the file, so it can be read with a small bounded seek instead
# of constructing a ZipInfo for every entry (zipfile.ZipFile's constructor
# does that -- expensive and unbounded for a crafted archive with thousands
# of entries, and it runs in the API process, outside the parser subprocess's
# RLIMIT_AS / wall timeout).
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_EOCD64_SIGNATURE = b"PK\x06\x06"
_EOCD_MAX_TAIL = 22 + 65535  # fixed record + max comment length
_MIN_CENTRAL_DIR_ENTRY_SIZE = 46  # every central-directory entry is at least this many bytes


def _zip_entry_count(path) -> tuple[int, int] | None:
    """Returns (total_entries, central_directory_size) read from the zip's
    EOCD record (following the ZIP64 locator/record when the 16-bit count or
    32-bit offsets overflow), or None when no EOCD signature is found --
    callers fall back to the existing is_zipfile/ZipFile handling for a
    malformed archive rather than inventing a new error path here."""
    size = Path(path).stat().st_size
    with Path(path).open("rb") as handle:
        tail_size = min(size, _EOCD_MAX_TAIL)
        handle.seek(size - tail_size)
        tail = handle.read(tail_size)
        index = tail.rfind(_EOCD_SIGNATURE)
        if index == -1 or len(tail) - index < 22:
            return None
        total_entries, cd_size, cd_offset = struct.unpack("<HII", tail[index + 10:index + 20])
        if total_entries != 0xFFFF and cd_size != 0xFFFFFFFF and cd_offset != 0xFFFFFFFF:
            return total_entries, cd_size
        locator_start = index - 20
        if locator_start < 0 or tail[locator_start:locator_start + 4] != _EOCD64_LOCATOR_SIGNATURE:
            return None
        (eocd64_offset,) = struct.unpack("<Q", tail[locator_start + 8:locator_start + 16])
        if eocd64_offset > size - 56:
            return None
        handle.seek(eocd64_offset)
        record = handle.read(56)
        if len(record) < 56 or record[:4] != _EOCD64_SIGNATURE:
            return None
        total_entries = struct.unpack("<Q", record[32:40])[0]
        cd_size = struct.unpack("<Q", record[40:48])[0]
        return total_entries, cd_size

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


# Entries under /media/ only skip the text cap when they are a recognized
# image/binary media type. A part renamed to look like it's under /media/ but
# with any other extension (e.g. a disguised document.xml) is still text.
MEDIA_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tif', 'tiff', 'emf', 'wmf', 'svg',
    'bin', 'wdp', 'jfif',
}


def check_container(path, max_bytes, max_chars):
    if Path(path).stat().st_size > max_bytes:
        raise DocumentLimitError('Document exceeds byte limit.')
    if is_zipfile(path):
        counted = _zip_entry_count(path)
        if counted is not None:
            entry_count, cd_size = counted
            if entry_count > 10000 or cd_size / _MIN_CENTRAL_DIR_ENTRY_SIZE > 10000:
                raise DocumentLimitError('Document archive exceeds expanded size limit.')
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000:
                raise DocumentLimitError('Document archive exceeds expanded size limit.')
            total_size = 0
            text_size = 0
            media_size = 0
            for info in entries:
                total_size += info.file_size
                name = info.filename.replace('\\', '/').lower()
                extension = name.rsplit('.', 1)[-1] if '.' in name else ''
                # Media (images/embedded binaries, already compressed) is excluded
                # from the text-expansion cap so a photo-heavy DOCX isn't rejected,
                # but it gets its own budget below -- without one, a media-only zip
                # bomb (declared-size-only, since it never hits the text cap) was
                # only caught by the much looser *10 overall cap.
                if '/media/' in name and extension in MEDIA_EXTENSIONS:
                    media_size += info.file_size
                else:
                    text_size += info.file_size
            if (text_size > max_chars * 4 or total_size > max_bytes * 10
                    or media_size > max_bytes * 4):
                raise DocumentLimitError('Document archive exceeds expanded size limit.')


def check_text(text, max_chars):
    if len(text) > max_chars:
        raise DocumentLimitError('Document exceeds extracted text limit.')
    return text


def parse_bounded(path, kind, max_bytes, max_chars, timeout,
                   ocr_fallback_mode='off', ocr_fallback_python=None, ocr_fallback_timeout=120):
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
        text = check_text(output.read_text(encoding='utf-8'), max_chars)
    if (kind == 'pdf' and not text.strip() and ocr_fallback_mode == 'auto'
            and ocr_fallback_python and Path(ocr_fallback_python).exists()):
        return _ocr_fallback(path, max_chars, ocr_fallback_timeout, ocr_fallback_python) or text
    return text


def _ocr_fallback(path, max_chars, timeout, ocr_python):
    """Best-effort: a broken or slow OCR venv must not break an upload whose primary
    parse already succeeded cleanly, so any failure here falls back to the caller's
    (empty) text instead of raising."""
    import os
    import subprocess
    import tempfile
    from config import REPO_ROOT
    with tempfile.TemporaryDirectory(prefix='claims-ocr-') as folder:
        output = Path(folder) / 'text.txt'
        passthrough = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR')
        env = {name: os.environ[name] for name in passthrough if name in os.environ}
        try:
            result = subprocess.run(
                [str(ocr_python), '-m', 'backend.ocr_fallback_worker',
                 str(Path(path).resolve()), str(output), str(max_chars)],
                cwd=REPO_ROOT, timeout=timeout, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0 or not output.exists():
            return None
        return check_text(output.read_text(encoding='utf-8'), max_chars)
