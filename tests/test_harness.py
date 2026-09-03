import json
import subprocess
from pathlib import Path

import pytest

from backend.harness import run_gate


def test_given_added_secret_pattern_when_secret_gate_runs_then_finding_is_blocking(tmp_path: Path):
    (tmp_path / "bad.py").write_text("API_KEY = 'secret-value-that-is-long-enough'", encoding="utf-8")
    findings = run_gate("secrets", tmp_path)
    assert any(f.rule_id == "secret-scan" and f.blocking for f in findings)


def test_given_clean_tree_when_secret_gate_runs_then_no_finding_is_returned(tmp_path: Path):
    (tmp_path / "good.py").write_text("API_KEY = os.environ['API_KEY']", encoding="utf-8")
    assert run_gate("secrets", tmp_path) == []


def test_given_approved_spec_when_spec_gate_runs_then_no_finding_is_returned(tmp_path: Path):
    specs = tmp_path / "docs" / "superpowers" / "specs"
    specs.mkdir(parents=True)
    (specs / "feature-design.md").write_text("# Design", encoding="utf-8")
    assert run_gate("specs", tmp_path) == []


def test_given_unknown_gate_when_run_then_it_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown gate"):
        run_gate("missing", tmp_path)


def test_given_clean_tree_when_lint_gate_runs_then_no_finding_is_returned(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert run_gate("lint", tmp_path) == []


def test_given_no_requirements_file_when_dependency_audit_gate_runs_then_no_finding_is_returned(tmp_path: Path):
    assert run_gate("dependency-audit", tmp_path) == []


def test_given_pip_audit_reports_a_vulnerability_when_dependency_audit_gate_runs_then_finding_is_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "requirements.txt").write_text("urllib3==1.24.1\n", encoding="utf-8")
    fake_report = {
        "dependencies": [
            {
                "name": "urllib3",
                "version": "1.24.1",
                "vulns": [{"id": "PYSEC-2019-133", "fix_versions": ["1.24.2"]}],
            }
        ]
    }

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout=json.dumps(fake_report), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    findings = run_gate("dependency-audit", tmp_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "dependency-audit"
    assert not finding.blocking
    assert "urllib3==1.24.1" in finding.message
    assert "PYSEC-2019-133" in finding.evidence


def test_given_pip_audit_finds_nothing_when_dependency_audit_gate_runs_then_no_finding_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "requirements.txt").write_text("requests==2.34.2\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"dependencies": []}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_gate("dependency-audit", tmp_path) == []


def test_given_clean_tree_when_static_security_gate_runs_then_no_finding_is_returned(tmp_path: Path):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert run_gate("static-security", tmp_path) == []


def test_given_high_severity_high_confidence_pattern_when_static_security_gate_runs_then_finding_is_blocking(
    tmp_path: Path,
):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "bad.py").write_text(
        "import subprocess\n\n\ndef run(cmd):\n    subprocess.call(cmd, shell=True)\n",
        encoding="utf-8",
    )
    findings = run_gate("static-security", tmp_path)
    assert any(f.rule_id == "static-security" and f.blocking and "B602" in f.message for f in findings)
    # The B404 "import subprocess" finding on the same file is LOW severity and must stay advisory.
    assert any(f.rule_id == "static-security" and not f.blocking for f in findings)
