"""
Regenerates assets/eval_results.png from eval/results.json's summary.

Run this after any eval/run_eval.py rerun so the README chart never goes
stale next to the numbers in its own table.

Usage:
    .venv/bin/python eval/plot_results.py
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt

# results.json holds only the latest run and no date, so the date the README
# quotes for it lives here. Update it with every eval/run_eval.py rerun.
LATEST_RUN_DATE = "2026-09-22"

# Faithfulness history isn't in results.json either -- these are committed
# values from docs/build-history.md / docs/current-state.md, kept only for the
# trend panel. Judge-model changes break comparability (current-state Known
# Issues), so each judge gets its own series and they are never joined.
FAITHFULNESS_OLD_JUDGE = "qwen2.5-14b-instruct-1m"
FAITHFULNESS_OLD = [0.735, 0.811, 0.854, 0.875]
FAITHFULNESS_OLD_LABELS = ["initial", "dossier-scoring fix", "corpus rebuild", "+ correctness metric"]
# Current judge: the 2026-09-03 re-baseline, then the latest run (appended from results.json).
FAITHFULNESS_NEW = [0.812]
FAITHFULNESS_NEW_LABELS = ["2026-09-03 re-baseline"]

ROOT = Path(__file__).resolve().parent.parent


def main():
    results_path = ROOT / "eval" / "results.json"
    summary = json.loads(results_path.read_text(encoding="utf-8"))["summary"]

    new_trend = FAITHFULNESS_NEW + [summary["faithfulness_avg"]]
    new_labels = FAITHFULNESS_NEW_LABELS + [LATEST_RUN_DATE]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    fig.suptitle(
        f"ClaimsRAG evaluation: {summary['n_queries']} golden queries, run {LATEST_RUN_DATE}, "
        f"local judge {summary['model']}",
        fontsize=12,
    )

    # Panel 1: Context Precision / Recall, naive vs hybrid+rerank
    ax = axes[0]
    metrics = ["Context\nPrecision", "Context\nRecall"]
    naive_vals = [summary["naive_precision_avg"], summary["naive_recall_avg"]]
    hybrid_vals = [summary["hybrid_precision_avg"], summary["hybrid_recall_avg"]]
    x = range(len(metrics))
    width = 0.35
    ax.bar([i - width / 2 for i in x], naive_vals, width, label="Naive (vector-only)", color="#94a3b8")
    ax.bar([i + width / 2 for i in x], hybrid_vals, width, label="Hybrid + Rerank", color="#2563eb")
    ax.set_xticks(list(x))
    ax.set_xticklabels(metrics)
    ax.set_ylim(0, 1.05)
    ax.set_title("Retrieval Quality")
    ax.legend(loc="lower right", fontsize=8)
    # Three decimals, matching the README table.
    for i, v in enumerate(naive_vals):
        ax.text(i - width / 2, v + 0.02, f"{v:.3f}", ha="center", fontsize=8)
    for i, v in enumerate(hybrid_vals):
        ax.text(i + width / 2, v + 0.02, f"{v:.3f}", ha="center", fontsize=8)

    # Panel 2: Faithfulness over time, one unjoined series per judge model
    ax = axes[1]
    old_x = list(range(len(FAITHFULNESS_OLD)))
    new_x = [len(old_x) + i for i in range(len(new_trend))]
    ax.plot(old_x, FAITHFULNESS_OLD, marker="o", color="#94a3b8", label=f"judge {FAITHFULNESS_OLD_JUDGE}")
    ax.plot(new_x, new_trend, marker="o", color="#16a34a", label=f"judge {summary['model']}")
    ax.axvline(len(old_x) - 0.5, color="#64748b", linestyle="--", linewidth=1)
    ax.text(len(old_x) - 0.45, 0.62, "judge changed:\nnot comparable", fontsize=7, color="#64748b")
    ax.set_xticks(old_x + new_x)
    ax.set_xticklabels(FAITHFULNESS_OLD_LABELS + new_labels, rotation=25, ha="right", fontsize=8)
    ax.set_ylim(0.6, 1.0)
    ax.set_title("Faithfulness Over Time")
    ax.legend(loc="upper left", fontsize=7)
    for i, v in zip(old_x + new_x, FAITHFULNESS_OLD + new_trend, strict=True):
        ax.text(i, v + 0.015, f"{v:.3f}", ha="center", fontsize=8)

    # Panel 3: Faithfulness vs reference-fact coverage -- the groundedness/correctness gap
    ax = axes[2]
    labels = ["Faithfulness\n(grounded in context)", "Reference-fact coverage\n(vs. verified reference)"]
    vals = [summary["faithfulness_avg"], summary["correctness_avg"]]
    colors = ["#16a34a", "#d97706"]
    bars = ax.bar(labels, vals, color=colors)
    ax.set_ylim(0, 1.05)
    ax.set_title("Groundedness vs. Correctness Gap")
    for bar, v in zip(bars, vals, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.02, f"{v:.3f}", ha="center", fontsize=8)

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out_path = ROOT / "assets" / "eval_results.png"
    fig.savefig(out_path, dpi=150)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
