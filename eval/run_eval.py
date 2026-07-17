"""
Domain-grounded retrieval + groundedness evaluation for AutoClaimsRAG.

Two things are measured, both against the golden query set in golden_queries.py:

1. Retrieval quality: naive (vector-only) vs. the app's actual hybrid
   (vector + FTS5 + RRF + cross-encoder rerank) pipeline, scored with Ragas
   Context Precision / Context Recall against a verified reference answer.
   This is the headline "did the hybrid-search work pay off" number.

2. Groundedness: Faithfulness of the live agentic router's actual generated
   answers (via /api/chat, online LM Studio mode) against the sources it
   cites — i.e. does the real system's output stay grounded in what it
   retrieved, not just in retrieval quality in isolation.

Judge model: whatever's currently loaded in LM Studio, detected the same way
the app itself does. Run this with the backend server already running
(`uvicorn backend.app:app --port 8000`) and LM Studio serving a model.

Usage:
    .venv/Scripts/python.exe eval/run_eval.py
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golden_queries import GOLDEN_QUERIES
from ragas_lm_studio import get_lm_studio_judge

APP_URL = "http://127.0.0.1:8000"
LM_STUDIO_URL = "http://127.0.0.1:1234"
TOP_K = 4
JUDGE_CONCURRENCY = 2


def get_loaded_model() -> str:
    r = requests.get(f"{LM_STUDIO_URL}/v1/models", timeout=5)
    r.raise_for_status()
    data = r.json()["data"]
    if not data:
        raise RuntimeError("No model loaded in LM Studio.")
    return data[0]["id"]


def search(query: str, claim_id, mode: str) -> list[dict]:
    r = requests.post(
        f"{APP_URL}/api/eval/search",
        json={"query": query, "claim_id": claim_id, "mode": mode, "top_k": TOP_K},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["matches"]


def chat(query: str, claim_id) -> dict:
    r = requests.post(
        f"{APP_URL}/api/chat",
        json={"query": query, "claim_id": claim_id, "engine": "lm-studio"},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


async def score_context_metrics(precision_metric, recall_metric, user_input, reference, retrieved_contexts):
    if not retrieved_contexts:
        return 0.0, 0.0
    try:
        prec = await precision_metric.ascore(
            user_input=user_input, reference=reference, retrieved_contexts=retrieved_contexts
        )
        rec = await recall_metric.ascore(
            user_input=user_input, retrieved_contexts=retrieved_contexts, reference=reference
        )
        return prec.value, rec.value
    except Exception as e:
        print(f"    ! scoring failed: {e}")
        return None, None


async def score_faithfulness(faithfulness_metric, user_input, response, retrieved_contexts):
    if not retrieved_contexts or not response:
        return None
    try:
        result = await faithfulness_metric.ascore(
            user_input=user_input, response=response, retrieved_contexts=retrieved_contexts
        )
        return result.value
    except Exception as e:
        print(f"    ! faithfulness scoring failed: {e}")
        return None


async def main():
    print("=== AutoClaimsRAG Domain-Grounded Evaluation ===\n")

    model = get_loaded_model()
    print(f"Judge model (LM Studio): {model}\n")

    from ragas.metrics.collections import ContextPrecision, ContextRecall, Faithfulness

    judge = get_lm_studio_judge(model)
    precision_metric = ContextPrecision(llm=judge)
    recall_metric = ContextRecall(llm=judge)
    faithfulness_metric = Faithfulness(llm=judge)

    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)

    async def bounded_context_score(*args):
        async with sem:
            return await score_context_metrics(precision_metric, recall_metric, *args)

    async def bounded_faithfulness(*args):
        async with sem:
            return await score_faithfulness(faithfulness_metric, *args)

    results = []
    start = time.time()

    for i, q in enumerate(GOLDEN_QUERIES):
        print(f"[{i+1}/{len(GOLDEN_QUERIES)}] {q['id']}")

        naive_matches = search(q["query"], q["claim_id"], "naive")
        hybrid_matches = search(q["query"], q["claim_id"], "hybrid_rerank")
        naive_contexts = [m["content"] for m in naive_matches]
        hybrid_contexts = [m["content"] for m in hybrid_matches]

        naive_prec, naive_rec = await bounded_context_score(q["query"], q["reference"], naive_contexts)
        hybrid_prec, hybrid_rec = await bounded_context_score(q["query"], q["reference"], hybrid_contexts)

        chat_result = None
        faithfulness = None
        try:
            chat_result = chat(q["query"], q["claim_id"])
            answer = chat_result.get("answer", "")
            sources = [s["content"] for s in chat_result.get("sources", [])]
            # The LLM is also grounded in the claim dossier markdown injected
            # directly into its prompt (not a search result, so it isn't in
            # `sources`). Score against both, or claim-scoped answers that
            # correctly cite dossier figures look unfaithful when they aren't.
            dossier = chat_result.get("claim_dossier")
            if dossier:
                sources = sources + [dossier]
            faithfulness = await bounded_faithfulness(q["query"], answer, sources)
        except Exception as e:
            print(f"    ! /api/chat failed: {e}")

        row = {
            "id": q["id"],
            "claim_id": q["claim_id"],
            "query": q["query"],
            "naive_precision": naive_prec,
            "naive_recall": naive_rec,
            "hybrid_precision": hybrid_prec,
            "hybrid_recall": hybrid_rec,
            "faithfulness": faithfulness,
            "naive_top_filenames": [m["filename"] for m in naive_matches],
            "hybrid_top_filenames": [m["filename"] for m in hybrid_matches],
        }
        results.append(row)

        print(f"    naive  P={naive_prec} R={naive_rec}")
        print(f"    hybrid P={hybrid_prec} R={hybrid_rec}")
        print(f"    faithfulness={faithfulness}")

    elapsed = time.time() - start
    print(f"\nCompleted {len(GOLDEN_QUERIES)} queries in {elapsed:.1f}s\n")

    def avg(key):
        vals = [r[key] for r in results if isinstance(r[key], (int, float))]
        return round(statistics.mean(vals), 3) if vals else None

    summary = {
        "model": model,
        "n_queries": len(GOLDEN_QUERIES),
        "naive_precision_avg": avg("naive_precision"),
        "naive_recall_avg": avg("naive_recall"),
        "hybrid_precision_avg": avg("hybrid_precision"),
        "hybrid_recall_avg": avg("hybrid_recall"),
        "faithfulness_avg": avg("faithfulness"),
        "elapsed_seconds": round(elapsed, 1),
    }

    print("=== Summary ===")
    print(f"Context Precision:  naive={summary['naive_precision_avg']}  hybrid+rerank={summary['hybrid_precision_avg']}")
    print(f"Context Recall:     naive={summary['naive_recall_avg']}  hybrid+rerank={summary['hybrid_recall_avg']}")
    print(f"Faithfulness (live agentic answers): {summary['faithfulness_avg']}")

    out_path = Path(__file__).resolve().parent / "results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
