"""Zip entry-count tests (M12c-D finding #12): the declared entry count must
come from the end-of-central-directory record, not from constructing a
ZipFile (which builds a ZipInfo per entry in the API process before the cap
is checked)."""

import zipfile

import pytest

import backend.document_limits as document_limits
from backend.document_limits import DocumentLimitError, check_container


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)


def test_given_zip_bomb_entry_count_when_checked_then_rejected_without_constructing_zipfile(tmp_path, monkeypatch):
    path = tmp_path / "bomb.zip"
    _write_zip(path, [(f"f{i}.txt", b"x") for i in range(10_001)])

    def _forbidden(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed before the entry-count cap is checked")

    monkeypatch.setattr(document_limits, "ZipFile", _forbidden)
    with pytest.raises(DocumentLimitError, match="expanded size limit"):
        check_container(path, max_bytes=10_000_000, max_chars=10_000_000)


def test_given_normal_small_zip_when_checked_then_passes_and_per_entry_checks_still_run(tmp_path):
    path = tmp_path / "report.docx"
    xml = b"<xml>content</xml>"
    _write_zip(path, [("word/document.xml", xml)])
    # Does not raise -- entry count is 1, and the existing per-entry expansion
    # checks (exercised in test_hardening_lows.py) still run afterward.
    check_container(path, max_bytes=10_000_000, max_chars=100_000)


def test_given_zip64_archive_when_checked_then_counted_from_zip64_record(tmp_path):
    """A real archive with more than 0xFFFF entries forces Python's zipfile
    writer to emit a genuine ZIP64 end-of-central-directory record, so the
    16-bit count in the regular EOCD can't be trusted and our reader must
    follow the ZIP64 locator/record instead."""
    path = tmp_path / "zip64.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for i in range(65_537):
            archive.writestr(f"{i}", b"")

    counted = document_limits._zip_entry_count(path)
    assert counted is not None
    entry_count, _ = counted
    assert entry_count == 65_537  # confirms the ZIP64 record, not the 16-bit count, was read

    with pytest.raises(DocumentLimitError, match="expanded size limit"):
        check_container(path, max_bytes=50_000_000, max_chars=10_000_000)


def test_given_zip_with_long_comment_when_checked_then_eocd_still_found(tmp_path):
    path = tmp_path / "commented.docx"
    _write_zip(path, [("word/document.xml", b"<xml>content</xml>")])
    with zipfile.ZipFile(path, "a") as archive:
        archive.comment = b"c" * 60_000
    # Must not raise despite the long comment pushing the EOCD search window.
    check_container(path, max_bytes=10_000_000, max_chars=100_000)


@pytest.mark.parametrize("offset", [0xFFFFFFFFFFFFFFFF, 1 << 40])
def test_given_zip64_locator_with_bad_offset_then_falls_back_instead_of_raising(tmp_path, offset):
    import struct

    # Minimal tail: ZIP64 locator pointing outside the file, then an EOCD
    # whose 16-bit count overflows (0xFFFF) so the locator is followed.
    locator = b"PK\x06\x07" + struct.pack("<IQI", 0, offset, 1)
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0, 0, 0)
    path = tmp_path / "crafted.zip"
    path.write_bytes(b"\x00" * 64 + locator + eocd)
    assert document_limits._zip_entry_count(path) is None
