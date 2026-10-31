"""Matching logic for scripts/check_flake.py's known-flake triage.

Exercises classify()/extract_failed_nodeids() directly (not via subprocess) since
the matching logic, not the `gh` call, is what's worth covering here; fetch_run_log
shells out to `gh` and is intentionally left to manual/CI verification.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_flake import classify, extract_failed_nodeids, load_registry

REGISTRY_PATH = ROOT / "tests" / "known_flakes.json"

SQLITE_FLAKE_NODEID = (
    "tests/test_ingestion_load.py::test_given_small_load_when_async_ingested_then_no_failures_and_throughput_ok"
)
POSTGRES_FLAKE_NODEID = (
    "tests/test_ingestion_load.py::test_given_backend_postgres_flag_when_small_load_run_then_no_failures"
)

REAL_SQLITE_FLAKE_LOG = """\
    def test_given_small_load_when_async_ingested_then_no_failures_and_throughput_ok():
        result = subprocess.run(
>       assert result.returncode == 0, f"load test failed:\\n{result.stdout}\\n{result.stderr}"
E       AssertionError: load test failed:
E         count=500 enqueue=3.38s process=7.69s total=11.07s throughput=45.2 docs/sec processed=500 stats={'processed': 500, 'indexed': 500, 'deleted': 0, 'rejected': 0, 'dead_lettered': 0, 'duplicates': 0, 'malformed': 0}
E         LOAD TEST FAILED: throughput 45.2 < floor 50.0
E       assert 1 == 0

=========================== short test summary info ============================
FAILED tests/test_ingestion_load.py::test_given_small_load_when_async_ingested_then_no_failures_and_throughput_ok - AssertionError: load test failed:
============ 1 failed, 1377 passed, 30 skipped, 1 xfailed in 41.42s ============
"""

REAL_POSTGRES_FLAKE_LOG = """\
>       assert result.returncode == 0, f"load test failed:\\n{result.stdout}\\n{result.stderr}"
E       AssertionError: load test failed:
E         count=50 enqueue=0.11s process=5.59s total=5.71s throughput=8.8 docs/sec processed=50 stats={'processed': 50, 'indexed': 50, 'deleted': 0, 'rejected': 0, 'dead_lettered': 0, 'duplicates': 0, 'malformed': 0}
E         LOAD TEST FAILED: throughput 8.8 < floor 10.0
E       assert 1 == 0

=========================== short test summary info ============================
FAILED tests/test_ingestion_load.py::test_given_backend_postgres_flag_when_small_load_run_then_no_failures - AssertionError: load test failed:
============ 1 failed, 1403 passed, 1 skipped, 1 xfailed in 49.79s =============
"""

NOVEL_FAILURE_SAME_TEST_LOG = """\
>       assert result.returncode == 0, f"load test failed:\\n{result.stdout}\\n{result.stderr}"
E       AssertionError: load test failed:
E         Traceback (most recent call last):
E           File "scripts/load_test_ingestion.py", line 10, in <module>
E             from backend.blob_store import LocalDocumentBlobStore
E         ModuleNotFoundError: No module named 'backend'

=========================== short test summary info ============================
FAILED tests/test_ingestion_load.py::test_given_small_load_when_async_ingested_then_no_failures_and_throughput_ok - AssertionError: load test failed:
============ 1 failed, 1377 passed, 30 skipped, 1 xfailed in 2.1s ============
"""

UNREGISTERED_FAILURE_LOG = """\
>       assert response.status_code == 200
E       assert 404 == 200

=========================== short test summary info ============================
FAILED tests/test_api_status.py::test_given_authenticated_request_then_status_is_ok - assert 404 == 200
============ 1 failed, 1500 passed in 12.0s ============
"""

ALL_PASSED_LOG = """\
============ 1500 passed, 30 skipped in 40.0s ============
"""


def test_given_real_sqlite_throughput_failure_log_when_classified_then_it_is_known_flake():
    registry = load_registry(REGISTRY_PATH)
    result = classify(REAL_SQLITE_FLAKE_LOG, registry)

    assert result["total_failed"] == 1
    assert result["novel"] == []
    assert len(result["known"]) == 1
    assert result["known"][0]["nodeid"] == SQLITE_FLAKE_NODEID


def test_given_real_postgres_throughput_failure_log_when_classified_then_it_is_known_flake():
    registry = load_registry(REGISTRY_PATH)
    result = classify(REAL_POSTGRES_FLAKE_LOG, registry)

    assert result["total_failed"] == 1
    assert result["novel"] == []
    assert len(result["known"]) == 1
    assert result["known"][0]["nodeid"] == POSTGRES_FLAKE_NODEID


def test_given_same_test_failing_for_a_different_reason_when_classified_then_it_is_novel():
    """A registered nodeid whose failure text doesn't match its known signature
    (e.g. a real ModuleNotFoundError instead of the throughput message) must not
    be waved through as the known flake."""
    registry = load_registry(REGISTRY_PATH)
    result = classify(NOVEL_FAILURE_SAME_TEST_LOG, registry)

    assert result["total_failed"] == 1
    assert result["known"] == []
    assert len(result["novel"]) == 1
    assert result["novel"][0]["nodeid"] == SQLITE_FLAKE_NODEID


def test_given_failure_with_no_registry_entry_when_classified_then_it_is_novel():
    registry = load_registry(REGISTRY_PATH)
    result = classify(UNREGISTERED_FAILURE_LOG, registry)

    assert result["total_failed"] == 1
    assert result["known"] == []
    assert len(result["novel"]) == 1
    assert result["novel"][0]["nodeid"] == "tests/test_api_status.py::test_given_authenticated_request_then_status_is_ok"


def test_given_log_with_no_failures_when_classified_then_nothing_is_reported():
    registry = load_registry(REGISTRY_PATH)
    result = classify(ALL_PASSED_LOG, registry)

    assert result == {"known": [], "novel": [], "total_failed": 0}


def test_given_repeated_failed_summary_lines_when_nodeids_extracted_then_deduplicated():
    log_text = REAL_SQLITE_FLAKE_LOG + REAL_SQLITE_FLAKE_LOG
    nodeids = extract_failed_nodeids(log_text)

    assert nodeids == [SQLITE_FLAKE_NODEID]


def test_given_registry_file_when_loaded_then_both_ingestion_load_flakes_are_present():
    registry = load_registry(REGISTRY_PATH)
    nodeids = {entry["nodeid"] for entry in registry}

    assert SQLITE_FLAKE_NODEID in nodeids
    assert POSTGRES_FLAKE_NODEID in nodeids
