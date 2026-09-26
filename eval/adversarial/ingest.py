"""Builds a scratch copy of the vector store and indexes the adversarial fixtures.

The live `rag_store.db` is never modified: the source is opened read-only and
copied with SQLite's own backup API, then the fixtures are written into the
copy. The process environment is repointed at the scratch paths *before*
`backend.rag_engine` is imported, because that module resolves `RAG_DB_PATH`
and `STORED_DOCUMENTS_DIR` at import time.

Usage:
    .venv/bin/python -B eval/adversarial/ingest.py [--source-db PATH] [--work-dir PATH]

Then start the app against the scratch store and run the suite:
    RAG_DB_PATH=<work-dir>/adv_store.db STORED_DOCUMENTS_DIR=<work-dir>/stored_documents \\
        AUDIT_LOG_PATH=<work-dir>/audit.log.jsonl JOBS_DB_PATH=<work-dir>/jobs.db \\
        SIMULATION_MODE=false .venv/bin/uvicorn backend.app:app --port 8001
    .venv/bin/python -B eval/run_adversarial_eval.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = REPO_ROOT / "eval" / "adversarial" / ".work"
SCRATCH_DB_NAME = "adv_store.db"
SCRATCH_STORAGE_NAME = "stored_documents"


def default_source_db() -> Path:
    """`$RAG_DB_PATH` when set, else the repo's own store."""
    configured = os.environ.get("RAG_DB_PATH")
    return Path(configured) if configured else REPO_ROOT / "rag_store.db"


def scratch_paths(work_dir: Path) -> tuple[Path, Path]:
    return work_dir / SCRATCH_DB_NAME, work_dir / SCRATCH_STORAGE_NAME


def uvicorn_command(scratch_db: Path, scratch_storage: Path) -> str:
    """The exact command that serves the harness app against a scratch store.

    Audit log and jobs DB are repointed into the work dir too, so an adversarial
    run never appends to the developer's real audit trail; SIMULATION_MODE=false
    makes a generator outage an error instead of a silently simulated answer.
    """
    work_dir = scratch_db.parent
    return (
        f"RAG_DB_PATH={scratch_db} STORED_DOCUMENTS_DIR={scratch_storage} "
        f"AUDIT_LOG_PATH={work_dir / 'audit.log.jsonl'} JOBS_DB_PATH={work_dir / 'jobs.db'} "
        "SIMULATION_MODE=false .venv/bin/uvicorn backend.app:app --port 8001"
    )


def _remove_scratch(scratch_db: Path, scratch_storage: Path) -> None:
    for path in (scratch_db, Path(f"{scratch_db}-wal"), Path(f"{scratch_db}-shm")):
        path.unlink(missing_ok=True)
    if scratch_storage.exists():
        shutil.rmtree(scratch_storage)


def _copy_store(source_db: Path, scratch_db: Path) -> None:
    """Read-only copy of the source store via the sqlite backup API."""
    source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(scratch_db))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _fixture_text(fixture: dict) -> str:
    """The raw adversarial document body -- not escaped, not altered."""
    sections = "\n\n".join(f"{heading}\n{body}" for heading, body in fixture["sections"])
    return fixture["title"] + "\n\n" + sections + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-db", default=None, help="vector store to copy (default: $RAG_DB_PATH or rag_store.db)")
    parser.add_argument("--work-dir", default=None, help=f"scratch directory (default: {DEFAULT_WORK_DIR})")
    args = parser.parse_args(argv)

    source_db = Path(args.source_db).resolve() if args.source_db else default_source_db().resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else DEFAULT_WORK_DIR.resolve()
    scratch_db, scratch_storage = scratch_paths(work_dir)

    if scratch_db == source_db:
        print(f"refusing to run: scratch DB {scratch_db} is the source DB {source_db}", file=sys.stderr)
        return 2
    if not source_db.exists():
        print(f"source database not found: {source_db}", file=sys.stderr)
        return 2

    work_dir.mkdir(parents=True, exist_ok=True)
    _remove_scratch(scratch_db, scratch_storage)
    _copy_store(source_db, scratch_db)

    # rag_engine resolves both paths at import time, so repoint the environment
    # before the first backend import.
    os.environ["RAG_DB_PATH"] = str(scratch_db)
    os.environ["STORED_DOCUMENTS_DIR"] = str(scratch_storage)
    sys.path.insert(0, str(REPO_ROOT))

    from backend.rag_engine import DocumentParser, EmbeddingEngine, SQLiteVectorStore
    from eval.adversarial.cases import ADVERSARIAL_FIXTURES
    from eval.adversarial.validate import validate
    from scripts.rebuild_golden_source_docs import _build_docx, _build_pdf

    problems = validate(ADVERSARIAL_FIXTURES, [])
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print(f"{len(problems)} fixture problem(s); nothing was indexed", file=sys.stderr)
        return 2

    store = SQLiteVectorStore(db_path=str(scratch_db), storage_dir=str(scratch_storage))
    engine = EmbeddingEngine()

    for fixture in ADVERSARIAL_FIXTURES:
        filename = fixture["filename"]
        file_type = fixture["file_type"]
        claim_id = fixture.get("claim_id")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, filename)
            if file_type == "txt":
                text = _fixture_text(fixture)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
            elif file_type == "docx":
                _build_docx(path, fixture["title"], fixture["sections"])
                text = DocumentParser.parse(path, file_type)
            elif file_type == "pdf":
                _build_pdf(path, fixture["title"], fixture["sections"])
                text = DocumentParser.parse(path, file_type)
            else:
                print(f"unsupported file_type {file_type!r} for {filename}", file=sys.stderr)
                return 2
            doc_id, parents = store.add_document(
                filename=filename,
                file_type=file_type,
                file_size=os.path.getsize(path),
                text=text,
                embedding_engine=engine,
                claim_id=claim_id,
                file_path=path,
            )
        print(f"Indexed {filename} (claim={claim_id or 'global'}): doc_id={doc_id}, parents={parents}")

    print(f"Scratch vector store: {scratch_db}")
    print(f"Scratch storage dir:  {scratch_storage}")
    print(f"Next: {uvicorn_command(scratch_db, scratch_storage)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
