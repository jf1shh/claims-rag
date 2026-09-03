"""CI smoke for the Phase 3.3 ingestion load test.

Runs the real scripts/load_test_ingestion.py at a small count so the script
itself (not a reimplementation) is exercised on every CI run; the full
--count 10000 run is a manual/ad-hoc verification. 500 docs with the fake
embedder completes in ~1-2s.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "load_test_ingestion.py"

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="POSTGRES_DSN not set; set it to a reachable Postgres + pgvector to run this case",
)


def test_given_small_load_when_async_ingested_then_no_failures_and_throughput_ok():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--count", "500", "--floor-docs-per-sec", "50.0"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"load test failed:\n{result.stdout}\n{result.stderr}"
    assert "LOAD TEST PASSED" in result.stdout
    # Sanity: the script reported the counts it actually processed.
    assert "processed=500" in result.stdout


@needs_postgres
def test_given_backend_postgres_flag_when_small_load_run_then_no_failures():
    """Phase 6.1: the same script, pointed at Postgres instead of the hardcoded
    SQLite store, so the one proven ingest-throughput tool also answers the
    Postgres/HNSW-scale question rather than needing a second harness."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--count",
            "50",
            "--floor-docs-per-sec",
            "10.0",
            "--backend",
            "postgres",
            "--postgres-dsn",
            POSTGRES_DSN,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"load test failed:\n{result.stdout}\n{result.stderr}"
    assert "LOAD TEST PASSED" in result.stdout
    assert "processed=50" in result.stdout
