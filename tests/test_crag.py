from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crag  # noqa: E402


# ---- evals ----


def test_evals_prints_golden_adversarial_and_guard_summaries(tmp_path, capsys, monkeypatch):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    adversarial_dir = eval_dir / "adversarial"
    adversarial_dir.mkdir()

    (eval_dir / "results.json").write_text(
        json.dumps(
            {
                "summary": {
                    "model": "qwen3-coder-30b-a3b-instruct",
                    "n_queries": 19,
                    "hybrid_precision_avg": 0.832,
                    "hybrid_recall_avg": 0.965,
                    "faithfulness_avg": 0.892,
                    "correctness_avg": 0.797,
                }
            }
        )
    )

    (eval_dir / "adversarial_results.json").write_text(
        json.dumps(
            {
                "run_at": "2026-09-26T08:19:04.165055+00:00",
                "n_cases": 27,
                "aggregates": {
                    "overall": {"pass_rate": 0.462},
                    "injection": {"pass_rate": 0.125, "attack_success_rate": 0.875},
                    "conflict": {"pass_rate": 1.0},
                },
            }
        )
    )
    # second adversarial file, missing the injection sub-keys -> should print "?"
    (eval_dir / "adversarial_results_fix2.json").write_text(
        json.dumps(
            {
                "run_at": "2026-09-27T00:00:00+00:00",
                "n_cases": 10,
                "aggregates": {
                    "overall": {"pass_rate": 0.9},
                    "conflict": {"pass_rate": 1.0},
                },
            }
        )
    )

    (eval_dir / "golden_guard_results.json").write_text(
        json.dumps(
            {
                "summary": {
                    "n": 19,
                    "errors": 0,
                    "withheld": 0,
                    "flagged": 0,
                    "conflicting_evidence": 0,
                }
            }
        )
    )

    rc = crag.main(["--root", str(tmp_path), "evals"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "golden: n=19 model=qwen3-coder-30b-a3b-instruct" in out
    assert "hybrid P/R=0.832/0.965" in out
    assert "faithfulness=0.892 correctness=0.797" in out
    assert "adversarial_results: run 2026-09-26 n=27" in out
    assert "attack success 0.875" in out
    assert "adversarial_results_fix2: run 2026-09-27 n=10" in out
    assert "injection ?" in out
    assert "(attack success ?)" in out
    assert "guard: n=19 errors=0 withheld=0 flagged=0 conflicting_evidence=0" in out


def test_evals_skips_missing_files(tmp_path, capsys):
    (tmp_path / "eval").mkdir()
    rc = crag.main(["--root", str(tmp_path), "evals"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


# ---- coverage ----


GOLDEN_QUERIES_SRC = '''
GOLDEN_QUERIES = [
    {"id": "a", "query": "What is the OEM parts policy for aftermarket OEM swaps?"},
    {"id": "b", "query": "When is a vehicle a total loss ruled, versus vacuum cleaned for resale?"},
    {"id": "c", "query": "Is rental reimbursement available during repairs?"},
]
'''


def test_coverage_flags_and_counts(tmp_path, capsys):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "golden_queries.py").write_text(GOLDEN_QUERIES_SRC)
    adversarial_dir = eval_dir / "adversarial"
    adversarial_dir.mkdir()
    (adversarial_dir / "case.json").write_text('{"text": "attempt subrogation bypass"}')

    rc = crag.main(["--root", str(tmp_path), "coverage"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "golden queries: 3" in out

    lines = {line.split(" | ")[0]: line for line in out.splitlines() if " | " in line}
    assert lines["OEM parts"] == "OEM parts | 2 | 0 | "
    assert lines["total loss"] == "total loss | 1 | 0 | thin"
    assert lines["subrogation"] == "subrogation | 0 | 1 | GAP"
    # "vacuum" must not count toward ACV (whole-word only)
    assert lines["total loss"].startswith("total loss | 1 |")


# ---- check ----


def test_check_parses_gate_line_errors_and_warnings(tmp_path, monkeypatch, capsys):
    calls = []

    class FakeProc:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        calls.append(cmd)
        if "pytest" in cmd:
            return FakeProc(0, "...\n5 passed in 0.12s\n")
        return FakeProc(
            1,
            "[error] secret-scan: possible credential in repository (foo.py:3)\n"
            "[warning] static-security: bandit finding (x)\n"
            "[warning] static-security: bandit finding (y)\n"
            "[warning] documentation: README.md is missing (README.md)\n"
            "foundation gate failed: 2 blocking finding(s)\n",
        )

    monkeypatch.setattr(crag.subprocess, "run", fake_run)

    rc = crag.main(["--root", str(tmp_path), "check"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "5 passed in 0.12s" in out
    assert "gate: failed, ? findings, 2 blocking" in out
    assert "[error] secret-scan: possible credential in repository (foo.py:3)" in out
    assert "warnings: documentation 1, static-security 2" in out
    assert len(calls) == 2


def test_check_tests_only_skips_gate(tmp_path, monkeypatch, capsys):
    class FakeProc:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        return FakeProc(0, "2 passed in 0.01s\n")

    monkeypatch.setattr(crag.subprocess, "run", fake_run)

    rc = crag.main(["--root", str(tmp_path), "check", "--tests-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 passed in 0.01s" in out
    assert "gate:" not in out


# ---- status ----


def test_status_uses_git_and_http_without_network(tmp_path, monkeypatch, capsys):
    class FakeProc:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0

    def fake_run(cmd, capture_output=None, text=None):
        if "rev-parse" in cmd:
            return FakeProc("feat/focus-devtools\n")
        if "status" in cmd:
            return FakeProc("M backend/harness.py\n?? scripts/crag.py\n")
        if "log" in cmd:
            return FakeProc("a45d91b Merge pull request #10\n")
        return FakeProc("")

    def fake_urlopen(url, timeout=None):
        raise crag.urllib.error.URLError("refused")

    monkeypatch.setattr(crag.subprocess, "run", fake_run)
    monkeypatch.setattr(crag.urllib.request, "urlopen", fake_urlopen)

    rc = crag.main(["--root", str(tmp_path), "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "branch: feat/focus-devtools" in out
    assert "changed files: 2" in out
    assert "last commit: a45d91b Merge pull request #10" in out
    assert "backend: down" in out
    assert "lemonade: down" in out


def test_find_python_prefers_venv(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_py = venv_bin / "python"
    fake_py.write_text("#!/bin/sh\n")
    assert crag.find_python(tmp_path) == str(fake_py)


def test_find_python_falls_back_to_sys_executable(tmp_path):
    assert crag.find_python(tmp_path) == sys.executable
