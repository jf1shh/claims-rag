"""Answerability probe: can a score tell answerable questions from absent ones?

Runs the sourced golden queries (answerable) and eval/absent_queries.py plus the
golden hallucination probe (absent) through the same search as
scripts/probe_retrieval.py, on a throwaway rebuild of the corpus. Two scores per
query, compared by AUC and by how many absent questions a cut-off catches while
keeping every answerable one:

* rerank: the best cross-encoder score in the shortlist (no LLM).
* judge:  the best P(yes) from backend/answerability.py over the passages the
          router would serve (the claim's own chunks plus the shortlist).
          Needs the local LLM; skip with --no-judge.
* joint:  one P(yes) with all served passages in one prompt (answers that need
          two passages); `either` is max(judge, joint).

`exempt` marks queries the router answers from computed claim tools (SIU,
subrogation, coverage audits); a gate would skip them, so the `*_gated`
summaries leave them out.

    .venv/bin/python scripts/probe_answerability.py --out eval/answerability-probe.json

Reads the source store and stored_documents/ only; never writes to them.
"""

import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import answerability  # noqa: E402
from backend.agentic_router import CLAIMS_DATA, AgenticRAGRouter  # noqa: E402
from backend.rag_engine import EmbeddingEngine, RerankingEngine, SQLiteVectorStore  # noqa: E402
from eval.absent_queries import ABSENT_QUERIES  # noqa: E402
from eval.golden_queries import GOLDEN_QUERIES  # noqa: E402
from scripts.probe_retrieval import _expected, rebuild  # noqa: E402

SHORTLIST = 8  # cross-encoder top passages handed to the judge


def queries():
    for row in GOLDEN_QUERIES:
        yield {**row, "answerable": bool(_expected(row))}
    for row in ABSENT_QUERIES:
        yield {**row, "answerable": False}


def exempt(row):
    """Mirrors AgenticRAGRouter._live_tool_section: a computed section exists."""
    if not row.get("claim_id"):
        return False
    audit = AgenticRAGRouter._detect_live_audit_type(row["query"])
    if audit == "general":
        return False
    return audit == "fraud" or any(c["id"] == row["claim_id"] for c in CLAIMS_DATA)


def passages_for(store, embedder, reranker, row, candidate_pool):
    matches = store.search_similarity(
        embedder.embed_query(row["query"]), row["query"], claim_id=row.get("claim_id"),
        reranking_engine=reranker, top_k=SHORTLIST, candidate_pool=candidate_pool, use_fts=True,
    )
    dossier = store.get_claim_chunks(row["claim_id"]) if row.get("claim_id") else []
    seen, out = set(), []
    for m in list(matches) + list(dossier):
        key = (m.get("filename"), m.get("content"))
        if key not in seen:
            seen.add(key)
            out.append(m)
    return matches, out


def auc(pos, neg):
    """P(score of a random answerable > score of a random absent), ties half."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def separation(rows, key):
    pos = [r[key] for r in rows if r["answerable"] and r[key] is not None]
    neg = [r[key] for r in rows if not r["answerable"] and r[key] is not None]
    if not pos or not neg:
        return None
    keep_all = min(pos)
    out = {"auc": auc(pos, neg), "answerable": len(pos), "absent": len(neg),
           "cutoff_keeping_all_answerable": round(keep_all, 4),
           "absent_caught_at_that_cutoff": sum(1 for n in neg if n < keep_all)}
    for cut in (0.3, 0.5):
        out[f"at_{cut}"] = {"answerable_kept": sum(1 for p in pos if p >= cut),
                            "absent_caught": sum(1 for n in neg if n < cut)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source-db", default="rag_store.db")
    ap.add_argument("--stored-dir", default="stored_documents")
    ap.add_argument("--embedding-model", default=os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    ap.add_argument("--candidate-pool", type=int, default=int(os.environ.get("RERANK_CANDIDATE_POOL", "15")))
    ap.add_argument("--llm-base-url", default=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:13305"))
    ap.add_argument("--llm-model", default=os.environ.get("LLM_MODEL"))
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    client = None
    if not args.no_judge:
        from backend.llm_client import OpenAICompatibleClient
        client = OpenAICompatibleClient(base_url=args.llm_base_url, default_model=args.llm_model or "local-model")

    embedder = EmbeddingEngine(args.embedding_model)
    reranker = RerankingEngine(device="cpu")
    rows = []
    with tempfile.TemporaryDirectory(prefix="crag-answerability-") as tmp:
        store = SQLiteVectorStore(db_path=os.path.join(tmp, "probe.db"), storage_dir=os.path.join(tmp, "docs"))
        n = rebuild(args.source_db, args.stored_dir, store, embedder)
        t0 = time.monotonic()
        for row in queries():
            matches, served = passages_for(store, embedder, reranker, row, args.candidate_pool)
            rerank = [m["rerank_score"] for m in matches if "rerank_score" in m]
            verdict = answerability.judge(client, row["query"], served) if client else None
            joint = answerability.judge_joint(client, row["query"], served) if client else None
            scores = [s for s in (verdict.best if verdict else None, joint) if s is not None]
            rows.append({
                "id": row["id"], "answerable": row["answerable"], "claim_id": row.get("claim_id"),
                "exempt": exempt(row),
                "rerank": round(max(rerank), 4) if rerank else None,
                "judge": round(verdict.best, 4) if verdict else None,
                "joint": round(joint, 4) if joint is not None else None,
                "either": round(max(scores), 4) if scores else None,
                "judged": [{"filename": p.get("filename"), "p": s} for p, s in
                           zip(served, verdict.scores if verdict else (), strict=False)],
            })
        elapsed = time.monotonic() - t0
    gated = [r for r in rows if not r["exempt"]]
    summary = {"documents": n, "seconds": round(elapsed, 1), "shortlist": SHORTLIST,
               "exempt": sum(r["exempt"] for r in rows)}
    for key in ("rerank", "judge", "joint", "either"):
        summary[key] = separation(rows, key)
        summary[f"{key}_gated"] = separation(gated, key)
    for r in sorted(rows, key=lambda r: (r["answerable"], r["either"] or 0)):
        print(f"{r['id']:<40} {'ANS' if r['answerable'] else 'ABS'}{' EX' if r['exempt'] else '   '} "
              f"rerank={r['rerank']} judge={r['judge']} joint={r['joint']}")
    print(json.dumps(summary, indent=1))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"summary": summary, "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main()
