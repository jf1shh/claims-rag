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
