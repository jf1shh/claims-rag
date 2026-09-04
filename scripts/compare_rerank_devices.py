"""Deterministic CPU-vs-GPU reranker diff over the golden query set.

Verification tool for any change that alters *how* the cross-encoder computes
rather than *what* it computes -- device moves (RERANK_DEVICE), kernel or
precision changes, a torch build swap. No LLM is involved: it runs the real
retrieval path twice with only the device differing and compares the passages
that come back, in order, plus their rerank scores.

This exists because the golden eval suite is the wrong instrument for that
question. Its judge introduces run-to-run noise large enough to swamp the
effect being tested (see docs/enterprise-migration.md Phase 6.1 -- per-query
correctness moved by up to +/-0.8 purely from a judge swap). Identical
retrieval output proves nothing downstream can differ, which is strictly
stronger evidence than any judged metric.

Usage:  .venv/bin/python scripts/compare_rerank_devices.py
Exit 0 if the two devices agree on every query, 1 otherwise.
"""

from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "eval")):
    if path not in sys.path:
        sys.path.insert(0, path)

from golden_queries import GOLDEN_QUERIES  # noqa: E402
from backend.rag_engine import EmbeddingEngine, RerankingEngine, SQLiteVectorStore  # noqa: E402
from config import Settings  # noqa: E402

# Below the smallest gap ever observed between adjacent passages, and well
# above float32 round-off between two devices running the same model.
SCORE_TOLERANCE = 1e-4


def fingerprint(match):
    return hashlib.sha1(match["content"].encode()).hexdigest()[:10]


def main():
    settings = Settings.from_env({})
    store = SQLiteVectorStore(str(settings.rag_db_path), str(settings.stored_documents_dir))
    # CPU embeddings on both arms so the query vectors are bit-identical and
    # the reranker is the only thing that varies.
    embedder = EmbeddingEngine()
    engines = {"cpu": RerankingEngine(device="cpu"), "gpu": RerankingEngine(device="cuda")}

    order_differences = 0
    max_delta = 0.0

    for query in GOLDEN_QUERIES:
        vector = embedder.embed_query(query["query"])
        results = {
            tag: store.search_similarity(
                vector,
                query["query"],
                claim_id=query["claim_id"],
                reranking_engine=engine,
                top_k=4,
                candidate_pool=settings.rerank_candidate_pool,
            )
            for tag, engine in engines.items()
        }
        cpu_order = [fingerprint(m) for m in results["cpu"]]
        gpu_order = [fingerprint(m) for m in results["gpu"]]
        if cpu_order != gpu_order:
            order_differences += 1
            print(f"  ORDER DIFF {query['id']}:\n    cpu={cpu_order}\n    gpu={gpu_order}")

        deltas = [
            abs(a.get("rerank_score", 0.0) - b.get("rerank_score", 0.0))
            for a, b in zip(results["cpu"], results["gpu"], strict=False)
        ]
        max_delta = max([max_delta, *deltas])

    print(
        f"\nqueries={len(GOLDEN_QUERIES)} order_differences={order_differences} "
        f"max_score_delta={max_delta:.2e} (tolerance {SCORE_TOLERANCE:.0e})"
    )
    ok = order_differences == 0 and max_delta < SCORE_TOLERANCE
    print("PASS: devices agree" if ok else "FAIL: devices disagree")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
