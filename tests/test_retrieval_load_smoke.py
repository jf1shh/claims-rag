"""Phase 6.1: CI smoke for scripts/run_retrieval_load_test.py.

Runs the real orchestration script (seed -> start server -> Locust ->
parse p95) at a trivial scale so the harness itself is exercised on every
CI run against the postgres job's own service container. Does NOT assert
against the 100ms p95 bar -- 200 docs / 3 users will trivially clear it
either way, so passing here proves the tool works, not that the real
Phase 6.1 target is met. The actual 100k-doc measurement is manual/ad-hoc,
recorded in docs/enterprise-migration.md, matching how the Phase 3.3
ingestion load test's full --count 10000 run is handled
(tests/test_ingestion_load.py).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_retrieval_load_test.py"

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="POSTGRES_DSN not set; set it to a reachable Postgres + pgvector to run this smoke test",
)


@needs_postgres
def test_given_small_load_when_retrieval_load_test_run_then_harness_reports_p95():
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--count", "200", "--tenants", "2",
            "--users", "3", "--spawn-rate", "3", "--duration", "10s",
            "--no-assert-p95",
            "--synthetic-models",
            "--tenant-prefix", "loadtest-ci-smoke",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"load test harness failed:\n{result.stdout}\n{result.stderr}"
    assert "LOAD TEST PASSED" in result.stdout
    assert "P95=" in result.stdout
    assert "SYNTHETIC MODELS: orchestration smoke only" in result.stdout


def test_given_synthetic_models_when_latency_gate_requested_then_rejected(capsys):
    from scripts.run_retrieval_load_test import main

    with pytest.raises(SystemExit) as error:
        main(["--synthetic-models"])
    assert error.value.code == 2
    assert "synthetic models require --no-assert-p95" in capsys.readouterr().err
