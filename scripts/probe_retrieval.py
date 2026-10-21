"""Retrieval rank probe: where do the golden queries' expected sources land?

Rebuilds the corpus of an existing SQLite store into a throwaway store (same
parser, chunker and embedding path as ingestion), runs every golden query
through the same search the /api/eval/search endpoint uses, and reports the
rank of each expected source. No LLM and no judge, so it takes about a minute
instead of a RAGAS run's ~30, and it is deterministic. Use it to screen a
retrieval change before paying for the full golden and adversarial suites.

    .venv/bin/python scripts/probe_retrieval.py                       # current settings
    .venv/bin/python scripts/probe_retrieval.py --out probe.json      # keep per-query ranks
    .venv/bin/python scripts/probe_retrieval.py --embedding-model BAAI/bge-small-en-v1.5

Reads the source store and stored_documents/ only; never writes to them.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.rag_engine import DocumentParser, EmbeddingEngine, RerankingEngine, SQLiteVectorStore  # noqa: E402
from eval.golden_queries import GOLDEN_QUERIES  # noqa: E402

HIT_AT = 4  # the router synthesizes from the top 4 matches


def _expected(row):
    src = row.get("source")
    if not src:
        return []
    return [src] if isinstance(src, str) else list(src)


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(text):
    return text.replace(",", "")


def _facts(reference):
    """The numbers in a reference answer (amounts, days, percentages, years):
    the checkable part of a claims answer. Served context that lacks one cannot
    support it, whatever the judge later says."""
    return sorted({_norm(n) for n in _NUMBER.findall(reference)})


def _read_text(path, file_type):
    if file_type == "txt":
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    return DocumentParser.parse(path, file_type)


def rebuild(source_db, stored_dir, target, embedder):
    """Re-ingest every document in source_db into the store `target`."""
    conn = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    try:
        docs = conn.execute(
            "SELECT filename, file_type, claim_id, storage_key FROM documents ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for filename, file_type, claim_id, storage_key in docs:
        path = os.path.join(stored_dir, storage_key)
        text = _read_text(path, file_type)
        target.add_document(filename, file_type, os.path.getsize(path), text, embedder,
                            claim_id=claim_id, file_path=path)
    return len(docs)


def probe(store, embedder, reranker, candidate_pool, top_k):
    rows = []
    for row in GOLDEN_QUERIES:
        expected = _expected(row)
        if not expected:
            continue
        matches = store.search_similarity(
            embedder.embed_query(row["query"]), row["query"], claim_id=row.get("claim_id"),
            reranking_engine=reranker, top_k=top_k, candidate_pool=candidate_pool, use_fts=True,
        )
        order = []
        for m in matches:
            if m["filename"] not in order:
                order.append(m["filename"])
        ranks = {src: (order.index(src) + 1 if src in order else None) for src in expected}
        # Pinning only guarantees a slot within top_k, so the served top 4 is
        # its own call, not a prefix of the top-15 ranking.
        served = store.search_similarity(
            embedder.embed_query(row["query"]), row["query"], claim_id=row.get("claim_id"),
            reranking_engine=reranker, top_k=HIT_AT, candidate_pool=candidate_pool, use_fts=True,
        )
        served_names = {m["filename"] for m in served}
        facts = _facts(row.get("reference", ""))
        # The router always adds the active claim's own chunks (the dossier)
        # ahead of the matches, so they count as served context too.
        dossier = store.get_claim_chunks(row["claim_id"]) if row.get("claim_id") else []
        served_text = _norm(" ".join(m.get("content", "") for m in list(dossier) + served))
        found = [f for f in facts if f in served_text]
        rows.append({"id": row["id"], "ranks": ranks, "served": all(src in served_names for src in expected),
                     "facts": len(facts), "facts_found": len(found),
                     "facts_missing": [f for f in facts if f not in found]})
    return rows


def summarize(rows, top_k):
    worst = [max((r if r is not None else top_k + 1) for r in row["ranks"].values()) for row in rows]
    rr = [1.0 / r if r else 0.0 for row in rows for r in row["ranks"].values()]
    return {
        "queries": len(rows),
        f"all_sources_in_top{HIT_AT}": sum(1 for w in worst if w <= HIT_AT),
        f"all_sources_served_at_top_k{HIT_AT}": sum(1 for row in rows if row["served"]),
        "sources": len(rr),
        "mrr": round(sum(rr) / len(rr), 4),
        "missing": sum(1 for row in rows for r in row["ranks"].values() if r is None),
        "fact_recall": round(sum(r["facts_found"] for r in rows) / max(1, sum(r["facts"] for r in rows)), 4),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source-db", default="rag_store.db")
    ap.add_argument("--stored-dir", default="stored_documents")
    ap.add_argument("--embedding-model", default=os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    ap.add_argument("--candidate-pool", type=int, default=int(os.environ.get("RERANK_CANDIDATE_POOL", "15")))
    ap.add_argument("--top-k", type=int, default=15)
    ap.add_argument("--out", help="write per-query ranks and the summary as JSON")
    args = ap.parse_args(argv)

    embedder = EmbeddingEngine(args.embedding_model)
    reranker = RerankingEngine(device="cpu")
    with tempfile.TemporaryDirectory(prefix="crag-probe-") as tmp:
        store = SQLiteVectorStore(db_path=os.path.join(tmp, "probe.db"), storage_dir=os.path.join(tmp, "docs"))
        t0 = time.monotonic()
        n = rebuild(args.source_db, args.stored_dir, store, embedder)
        t1 = time.monotonic()
        rows = probe(store, embedder, reranker, args.candidate_pool, args.top_k)
    summary = summarize(rows, args.top_k)
    summary.update(documents=n, ingest_s=round(t1 - t0, 1), embedding_model=args.embedding_model)
    for row in rows:
        ranks = ", ".join(f"{k}={v if v else '-'}" for k, v in row["ranks"].items())
        gap = "" if row["facts_found"] == row["facts"] else f" FACTS {row['facts_found']}/{row['facts']}"
        print(f"{row['id']:<40} {'ok  ' if row['served'] else 'MISS'} {ranks}{gap}")
    print(json.dumps(summary))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"summary": summary, "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main()
