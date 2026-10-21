"""CLI tests for scripts/purge_claim.py (M12c-E): dry run deletes nothing,
--yes deletes, a conflict exits 2, and --sweep-jobs expires old terminal
rows. Builds real sqlite stores at the paths the CLI's own settings resolve
to, so main() exercises the same build_dependencies() path as production."""

import hashlib
import importlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.ingestion import IngestionJob, IngestionStatus
from backend.job_store import SqliteJobStore
from backend.rag_engine import SQLiteVectorStore


class _FakeEmbedder:
    def _vec(self, text):
        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.db"))
    monkeypatch.setenv("STORED_DOCUMENTS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("TENANT_ID", "tenant-a")
    monkeypatch.setenv("VECTOR_STORE", "sqlite")
    monkeypatch.setenv("QUEUE_PROVIDER", "in-process")
    monkeypatch.setenv("SIMULATION_MODE", "true")

    import scripts.purge_claim as purge_claim_cli

    importlib.reload(purge_claim_cli)

    store = SQLiteVectorStore(db_path=str(tmp_path / "rag.db"), storage_dir=str(tmp_path / "docs"))
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    return purge_claim_cli, store, job_store, tmp_path


def _add_job(job_store, claim_id, job_id="job_1", status=IngestionStatus.indexed, tenant_id="tenant-a"):
    now = datetime.now(timezone.utc)
    job_store.create(
        IngestionJob(
            job_id=job_id, tenant_id=tenant_id, document_id="doc_1", claim_id=claim_id,
            filename="report.pdf", checksum="sha256:x", status=status,
            created_at=now, updated_at=now,
        )
    )


def test_given_no_yes_flag_when_run_then_dry_run_deletes_nothing(cli_env, capsys):
    cli, store, job_store, _ = cli_env
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1")

    exit_code = cli.main(["--claim-id", "claim-1"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "1 document" in out
    assert "1 job row" in out
    assert len(store.get_claim_documents("claim-1")) == 1
    job_store.get("job_1", "tenant-a")  # still present


def test_given_yes_flag_when_run_then_claim_is_purged(cli_env):
    cli, store, job_store, _ = cli_env
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1")

    exit_code = cli.main(["--claim-id", "claim-1", "--yes"])

    assert exit_code == 0
    assert store.get_claim_documents("claim-1") == []
    with pytest.raises(KeyError):
        job_store.get("job_1", "tenant-a")


def test_given_active_job_when_run_with_yes_then_conflict_exit_code(cli_env):
    cli, store, job_store, _ = cli_env
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1", status=IngestionStatus.queued)

    exit_code = cli.main(["--claim-id", "claim-1", "--yes"])

    assert exit_code == 2
    assert len(store.get_claim_documents("claim-1")) == 1


def test_given_invalid_claim_id_then_exit_code_one(cli_env):
    cli, _, _, _ = cli_env
    assert cli.main(["--claim-id", "a" * 129, "--yes"]) == 1


def test_given_sweep_jobs_flag_when_run_then_old_terminal_rows_expired(cli_env, capsys):
    cli, store, job_store, _ = cli_env
    old = datetime.now(timezone.utc) - timedelta(days=31)
    job_store.create(
        IngestionJob(
            job_id="job_old", tenant_id="tenant-a", document_id="doc_1", claim_id=None,
            filename="old.txt", checksum="sha256:x", status=IngestionStatus.indexed,
            created_at=old, updated_at=old,
        )
    )

    exit_code = cli.main(["--sweep-jobs"])

    assert exit_code == 0
    assert "Swept 1" in capsys.readouterr().out
    with pytest.raises(KeyError):
        job_store.get("job_old", "tenant-a")
