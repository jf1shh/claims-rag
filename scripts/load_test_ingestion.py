"""Throughput load test for the async ingestion pipeline (Phase 3.3).

Pushes ``--count`` synthetic documents through the full async path --
``IngestionService.submit`` (dedupe + enqueue) -> in-process queue ->
``IngestionWorker`` (fetch blob -> parse -> chunk -> embed -> upsert) ->
``SQLiteVectorStore`` -- using a torch-free deterministic fake embedder so the
measurement is of the pipeline machinery, not the ML stack. Reports docs/sec,
enqueue time, processing time, and the worker's job-metrics counters. Exits
non-zero when any message failed/dead-lettered or throughput is below
``--floor-docs-per-sec``.

Run:  python scripts/load_test_ingestion.py --count 10000
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# All backend imports need ROOT on sys.path first (E402).
from backend.blob_store import LocalDocumentBlobStore  # noqa: E402
from backend.ingestion import IngestionService  # noqa: E402
from backend.ingestion_worker import IngestionWorker  # noqa: E402
from backend.job_store import SqliteJobStore  # noqa: E402
from backend.queue import InProcessQueue  # noqa: E402
from backend.rag_engine import SQLiteVectorStore  # noqa: E402


class _FakeEmbedder:
    """Deterministic, torch-free embedder (mirrors the test suites)."""

    @staticmethod
    def _vec(text: str):
        # usedforsecurity=False: this hash only needs to be a fast, deterministic
        # fingerprint for fake embedding vectors, never a security control.
        digest = hashlib.md5((text or "").encode(), usedforsecurity=False).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


def _synthetic_text(index: int) -> str:
    """A few sentences of stable, index-unique text so nothing dedupes and the
    corpus is realistic enough to chunk (each doc -> ~1 parent chunk)."""
    return (
        f"Auto claims reference document number {index}. The regional labor "
        f"rate schedule applies and the mechanical labor cap for this region "
        f"is $110 per hour as of the current year. Sheet metal and refinishing "
        f"are capped separately at $62 per hour, and glass endorsement terms "
        f"waive the comprehensive deductible for safety glass replacement. "
        f"Claim adjusters must verify the applicable state statute before "
        f"approving any estimate line item."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10_000, help="number of documents to ingest")
    parser.add_argument("--floor-docs-per-sec", type=float, default=50.0, help="minimum throughput to pass")
    args = parser.parse_args(argv)
    if args.count <= 0:
        parser.error("--count must be positive")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        blob = LocalDocumentBlobStore(root / "blobs")
        store = SQLiteVectorStore(db_path=str(root / "test.db"), storage_dir=str(root / "docs"), blob_store=blob)
        job_store = SqliteJobStore(str(root / "jobs.db"))
        queue = InProcessQueue()
        service = IngestionService(queue=queue, job_store=job_store)
        worker = IngestionWorker(
            queue=queue,
            vector_store=store,
            blob_store=blob,
            embedding_engine_factory=_FakeEmbedder,
            job_store=job_store,
        )

        start = time.perf_counter()
        for index in range(args.count):
            filename = f"doc_{index:05d}.txt"
            content = _synthetic_text(index).encode()
            blob.put(f"global/{filename}", content, "text/plain")
            service.submit(tenant_id="tenant-a", filename=filename, content=content)
        enqueue_done = time.perf_counter()

        processed = worker.run_once()
        done = time.perf_counter()

        total = done - start
        stats = worker.stats()
        docs_per_sec = args.count / total if total else 0.0

        print(
            f"count={args.count} enqueue={enqueue_done - start:.2f}s "
            f"process={done - enqueue_done:.2f}s total={total:.2f}s "
            f"throughput={docs_per_sec:.1f} docs/sec processed={processed} "
            f"stats={stats}"
        )

        failed = stats["dead_lettered"] + stats["malformed"] + stats["rejected"]
        problems = []
        if processed != args.count:
            problems.append(f"processed {processed} != count {args.count}")
        if failed:
            problems.append(f"{failed} message(s) failed: {stats}")
        if docs_per_sec < args.floor_docs_per_sec:
            problems.append(f"throughput {docs_per_sec:.1f} < floor {args.floor_docs_per_sec}")
        if problems:
            print(f"LOAD TEST FAILED: {'; '.join(problems)}")
            return 1
        print("LOAD TEST PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
