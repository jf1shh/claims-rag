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

3. Correctness: FactualCorrectness (mode="recall") of the live answer against
   the verified `reference` answer in golden_queries.py. This exists because
   Faithfulness alone missed a real bug (see CLAUDE.md Debugging History,
   chen-custom-equipment-cap): an answer can be perfectly grounded in
   retrieved context and still be wrong if it only reasons about one of
   several relevant line items. Faithfulness checks response-vs-context;
   this checks response-vs-ground-truth — what fraction of the reference's
   claims are actually covered by the response — so a response that drops a
   claim present in the reference now measurably loses recall even when
   every claim it does make is true. Deliberately NOT mode="f1"/"precision":
   an early run showed verbose-but-correct answers scoring low because
   precision penalizes true elaboration pulled from other retrieved sources
   that simply isn't in the terse reference text. Faithfulness already
   covers unsupported/fabricated claims; recall is the missing piece.

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
    """Return the id of a chat model actually resident in LM Studio.

    `/v1/models` lists what is *available*, not what is *loaded* — it returns
    every model on disk even when none is resident, so a check against it
    always passes and the first completion then fails with
    `400 - No models loaded`. `/api/v0/models` carries a real `state` field
    (`loaded` / `not-loaded`), so gate on that instead.
    """
    try:
        r = requests.get(f"{LM_STUDIO_URL}/api/v0/models", timeout=5)
        r.raise_for_status()
        entries = r.json()["data"]
    except Exception:
        # Not LM Studio (or too old for /api/v0) — fall back to the OpenAI
        # listing, which cannot distinguish loaded from available.
        r = requests.get(f"{LM_STUDIO_URL}/v1/models", timeout=5)
        r.raise_for_status()
        data = r.json()["data"]
        if not data:
            raise RuntimeError("No models served at {}.".format(LM_STUDIO_URL)) from None
        return data[0]["id"]

    loaded = [m for m in entries if m.get("state") == "loaded"]
    if not loaded:
        available = ", ".join(m["id"] for m in entries) or "(none)"
        raise RuntimeError(
            "No model is loaded in LM Studio — {} lists models but none are "
            "resident.\nAvailable: {}\nLoad one first, e.g.:\n"
            "  lms load qwen3-coder-30b-a3b-instruct --gpu max -c 8192 --parallel 4 --yes"
            .format(LM_STUDIO_URL, available)
        )

    # Prefer a chat model; embedding models are loaded alongside but cannot
    # answer the golden queries.
    chat = [m for m in loaded if m.get("type") != "embeddings"]
    return (chat or loaded)[0]["id"]


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


async def score_correctness(correctness_metric, response, reference):
    if not response or not reference:
        return None
    try:
        result = await correctness_metric.ascore(response=response, reference=reference)
        return result.value
    except Exception as e:
        print(f"    ! correctness scoring failed: {e}")
        return None


async def main():
    print("=== AutoClaimsRAG Domain-Grounded Evaluation ===\n")

    model = get_loaded_model()
    print(f"Judge model (LM Studio): {model}\n")

    from ragas.metrics.collections import ContextPrecision, ContextRecall, Faithfulness, FactualCorrectness

    judge = get_lm_studio_judge(model)
    precision_metric = ContextPrecision(llm=judge)
    recall_metric = ContextRecall(llm=judge)
    faithfulness_metric = Faithfulness(llm=judge)
    # mode="recall" (not the default "f1"): a first f1 run showed verbose-but-
    # correct answers scoring low because precision penalizes any elaboration
    # not in the terse `reference` text (extra true context pulled from other
    # retrieved sources counts as an unsupported claim). Faithfulness already
    # catches unsupported/fabricated claims against retrieved context; what's
    # missing and worth adding here is recall — did the answer cover every
    # fact the reference says it must — which is exactly the failure mode
    # (chen-custom-equipment-cap: one line item reasoned about, one dropped)
    # that motivated this metric in the first place.
    correctness_metric = FactualCorrectness(llm=judge, mode="recall")

    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)

    async def bounded_context_score(*args):
        async with sem:
            return await score_context_metrics(precision_metric, recall_metric, *args)

    async def bounded_faithfulness(*args):
        async with sem:
            return await score_faithfulness(faithfulness_metric, *args)

    async def bounded_correctness(*args):
        async with sem:
            return await score_correctness(correctness_metric, *args)

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
        correctness = None
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
            correctness = await bounded_correctness(answer, q["reference"])
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
            "correctness": correctness,
            "naive_top_filenames": [m["filename"] for m in naive_matches],
            "hybrid_top_filenames": [m["filename"] for m in hybrid_matches],
        }
        results.append(row)

        print(f"    naive  P={naive_prec} R={naive_rec}")
        print(f"    hybrid P={hybrid_prec} R={hybrid_rec}")
        print(f"    faithfulness={faithfulness}  correctness={correctness}")

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
        "correctness_avg": avg("correctness"),
        "elapsed_seconds": round(elapsed, 1),
        "metric_samples": {key: {"scored": sum(isinstance(r[key], (int, float)) for r in results),
                                 "failed": sum(not isinstance(r[key], (int, float)) for r in results)}
                           for key in ("naive_precision", "naive_recall", "hybrid_precision", "hybrid_recall", "faithfulness", "correctness")},
        "correctness_mode": "recall (reference-fact coverage)",
    }

    print("=== Summary ===")
    print(f"Context Precision:  naive={summary['naive_precision_avg']}  hybrid+rerank={summary['hybrid_precision_avg']}")
    print(f"Context Recall:     naive={summary['naive_recall_avg']}  hybrid+rerank={summary['hybrid_recall_avg']}")
    print(f"Faithfulness (live agentic answers): {summary['faithfulness_avg']}")
    print(f"Factual Correctness (live agentic answers vs. reference): {summary['correctness_avg']}")

    out_path = Path(__file__).resolve().parent / "results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
