"""Build a review packet for a human claims expert to score live agentic answers.

Part of closing the governance gap "Validation on real claims with expert review"
(docs/governance/naic-ai-bulletin-map.md, gap 2). That gap has two independent
halves: real (not synthetic) claim files, which need an insurer pilot and stay a
documented Gap, and expert review of answers, which this script makes possible now
by putting a domain expert's judgment -- not another LLM judge -- in the loop.

Samples the golden set (every claim-scoped query once, plus a deterministic random
slice of global queries), calls the live /api/chat endpoint for each (same path
eval/run_eval.py uses), and writes a Markdown packet with the question, the live
answer, its cited sources, the existing automated scores for comparison, and blank
fields for the expert to fill in. See docs/governance/expert-review-protocol.md for
the rubric and how to use the output.

Usage (server already running, a model loaded in Lemonade):
    .venv/bin/python eval/build_expert_review_packet.py
    .venv/bin/python eval/build_expert_review_packet.py --n-global 10 --out /tmp/packet.md
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golden_queries import GOLDEN_QUERIES  # noqa: E402

DEFAULT_SEED = 42


def sample_queries(queries, n_global=8, seed=DEFAULT_SEED):
    """One query per distinct claim_id, plus a deterministic random slice of the
    claim_id=None (global) queries. Covers every claim dossier at least once
    without growing the packet past what one reviewer can get through in a sitting.
    """
    by_claim = {}
    globals_ = []
    for q in queries:
        if q["claim_id"] is None:
            globals_.append(q)
        else:
            by_claim.setdefault(q["claim_id"], q)  # first query per claim wins

    rng = random.Random(seed)
    picked_globals = rng.sample(globals_, k=min(n_global, len(globals_)))
    return list(by_claim.values()) + picked_globals


def build_rows(queries, chat_fn):
    """Call chat_fn(query, claim_id) -> dict (the /api/chat response shape) for
    each sampled query and attach it to the golden-query record.
    """
    rows = []
    for q in queries:
        result = chat_fn(q["query"], q["claim_id"])
        rows.append({**q, "chat_result": result})
    return rows


def format_packet(rows, scored_by_id=None):
    """Render the Markdown review packet. `scored_by_id` optionally maps a golden
    query id to its automated faithfulness/correctness scores (eval/results-*.json
    "results" rows), shown for comparison -- the expert is not asked to agree with
    them, only to apply independent domain judgment.
    """
    scored_by_id = scored_by_id or {}
    lines = [
        "# ClaimsRAG expert review packet",
        "",
        f"{len(rows)} questions. See docs/governance/expert-review-protocol.md for the rubric.",
        "Fill in every `_Score:_` and `_Notes:_` line; leave everything else as-is.",
        "",
    ]
    for row in rows:
        chat_result = row.get("chat_result") or {}
        answer = chat_result.get("answer", "(no answer -- chat call failed or returned nothing)")
        sources = [s.get("filename", s.get("content", "")[:60]) for s in chat_result.get("sources", [])]
        auto = scored_by_id.get(row["id"], {})

        lines += [
            f"## {row['id']}",
            "",
            f"**Claim:** {row['claim_id'] or '(global, no claim)'}",
            "",
            f"**Question:** {row['query']}",
            "",
            f"**Reference answer (synthetic ground truth):** {row['reference']}",
            "",
            f"**Live answer:** {answer}",
            "",
            f"**Cited sources:** {', '.join(sources) if sources else '(none)'}",
            "",
            f"**Automated judge (for comparison only):** faithfulness={auto.get('faithfulness')}, "
            f"correctness={auto.get('correctness')}",
            "",
            "**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_",
            "",
            "**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_",
            "",
            "**What would a handler catch that the automated judge wouldn't?:** _Notes:_",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


def _load_auto_scores(results_path):
    if not results_path or not Path(results_path).exists():
        return {}
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    return {r["id"]: r for r in data.get("results", [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-global", type=int, default=8)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "expert_review_packet.md"))
    parser.add_argument("--results", default=str(Path(__file__).resolve().parent / "results.json"),
                         help="Automated eval results.json to show for comparison (optional).")
    args = parser.parse_args()

    from run_eval import chat  # live call; only needed when actually generating a packet

    queries = sample_queries(GOLDEN_QUERIES, n_global=args.n_global, seed=args.seed)
    rows = build_rows(queries, chat)
    scored_by_id = _load_auto_scores(args.results)
    packet = format_packet(rows, scored_by_id)

    out_path = Path(args.out)
    out_path.write_text(packet, encoding="utf-8")
    print(f"Wrote {len(rows)} questions to {out_path}")


if __name__ == "__main__":
    main()
