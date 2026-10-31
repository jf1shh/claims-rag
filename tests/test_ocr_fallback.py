"""OCR fallback (backend/ocr_fallback_worker.py): parse_bounded re-runs a PDF whose primary
extraction came back empty under a separate, operator-provisioned Docling venv. These tests
never install or import docling -- that stays a 6GB+ dependency kept out of CI entirely; they
mock subprocess.run to stand in for both the primary parser subprocess and the OCR one."""
from pathlib import Path
import subprocess

from backend.document_limits import parse_bounded


def _fake_run(primary_text, ocr_text=None, ocr_returncode=0, calls=None):
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        output = Path(cmd[-2])
        if 'backend.ocr_fallback_worker' in cmd:
            if ocr_returncode == 0:
                output.write_text(ocr_text, encoding='utf-8')
            return _Completed(ocr_returncode)
        output.write_text(primary_text, encoding='utf-8')
        return _Completed(0)
    return run


class _Completed:
    def __init__(self, returncode):
        self.returncode = returncode


def test_given_empty_text_and_ocr_configured_when_parsed_then_ocr_fallback_text_is_used(tmp_path, monkeypatch):
    pdf = tmp_path / 'scanned.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    ocr_python = tmp_path / 'ocr-venv' / 'bin' / 'python'
    ocr_python.parent.mkdir(parents=True)
    ocr_python.touch()
    calls = []
    monkeypatch.setattr(subprocess, 'run', _fake_run('', 'OCR extracted text', calls=calls))

    text = parse_bounded(pdf, 'pdf', 10_000_000, 10_000, 30,
                          ocr_fallback_mode='auto', ocr_fallback_python=ocr_python, ocr_fallback_timeout=30)

    assert text == 'OCR extracted text'
    assert any('backend.ocr_fallback_worker' in cmd for cmd in calls)


def test_given_empty_text_and_ocr_not_configured_when_parsed_then_stays_empty_without_crashing(tmp_path, monkeypatch):
    pdf = tmp_path / 'scanned.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    calls = []
    monkeypatch.setattr(subprocess, 'run', _fake_run('', calls=calls))

    text = parse_bounded(pdf, 'pdf', 10_000_000, 10_000, 30,
                          ocr_fallback_mode='auto', ocr_fallback_python=None, ocr_fallback_timeout=30)

    assert text == ''
    assert not any('backend.ocr_fallback_worker' in cmd for cmd in calls)


def test_given_non_empty_text_when_parsed_then_ocr_fallback_is_never_invoked(tmp_path, monkeypatch):
    pdf = tmp_path / 'real.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    ocr_python = tmp_path / 'ocr-venv' / 'bin' / 'python'
    ocr_python.parent.mkdir(parents=True)
    ocr_python.touch()
    calls = []
    monkeypatch.setattr(subprocess, 'run', _fake_run('Real extracted text', 'OCR text', calls=calls))

    text = parse_bounded(pdf, 'pdf', 10_000_000, 10_000, 30,
                          ocr_fallback_mode='auto', ocr_fallback_python=ocr_python, ocr_fallback_timeout=30)

    assert text == 'Real extracted text'
    assert not any('backend.ocr_fallback_worker' in cmd for cmd in calls)


def test_given_ocr_subprocess_fails_when_parsed_then_falls_back_to_original_empty_text(tmp_path, monkeypatch):
    pdf = tmp_path / 'scanned.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    ocr_python = tmp_path / 'ocr-venv' / 'bin' / 'python'
    ocr_python.parent.mkdir(parents=True)
    ocr_python.touch()
    monkeypatch.setattr(subprocess, 'run', _fake_run('', ocr_returncode=2))

    text = parse_bounded(pdf, 'pdf', 10_000_000, 10_000, 30,
                          ocr_fallback_mode='auto', ocr_fallback_python=ocr_python, ocr_fallback_timeout=30)

    assert text == ''


def test_given_mode_off_when_parsed_then_ocr_fallback_is_never_invoked_even_if_configured(tmp_path, monkeypatch):
    pdf = tmp_path / 'scanned.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake')
    ocr_python = tmp_path / 'ocr-venv' / 'bin' / 'python'
    ocr_python.parent.mkdir(parents=True)
    ocr_python.touch()
    calls = []
    monkeypatch.setattr(subprocess, 'run', _fake_run('', 'OCR text', calls=calls))

    text = parse_bounded(pdf, 'pdf', 10_000_000, 10_000, 30,
                          ocr_fallback_mode='off', ocr_fallback_python=ocr_python, ocr_fallback_timeout=30)

    assert text == ''
    assert not any('backend.ocr_fallback_worker' in cmd for cmd in calls)
