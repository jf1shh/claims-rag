"""
Regenerates assets/eval_results.png from eval/results.json's summary.

Run this after any eval/run_eval.py rerun so the README chart never goes
stale next to the numbers in its own table.

Usage:
    .venv/Scripts/python.exe eval/plot_results.py
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt

# Faithfulness's historical progression isn't in results.json (which only
# holds the latest run) -- these are the three prior committed values from
# CLAUDE.md's Debugging History / Build Plan, kept here only for the chart's
# trend line. Update this tuple by hand if a future session adds another
# fix-driven Faithfulness data point worth showing.
FAITHFULNESS_HISTORY = [0.735, 0.811, 0.854, 0.875]
FAITHFULNESS_HISTORY_LABELS = ["initial", "dossier-scoring fix", "corpus rebuild", "+ correctness metric"]

ROOT = Path(__file__).resolve().parent.parent


def main():
    results_path = ROOT / "eval" / "results.json"
    summary = json.loads(results_path.read_text(encoding="utf-8"))["summary"]

    faithfulness_trend = FAITHFULNESS_HISTORY + [summary["faithfulness_avg"]]
    faithfulness_labels = FAITHFULNESS_HISTORY_LABELS + ["fts-rank fix rerun"]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    fig.suptitle("AutoClaimsRAG Evaluation Results (19 golden queries, local LM Studio judge)", fontsize=12)

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
    for i, v in enumerate(naive_vals):
        ax.text(i - width / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
    for i, v in enumerate(hybrid_vals):
        ax.text(i + width / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)

    # Panel 2: Faithfulness trend across fixes
    ax = axes[1]
    ax.plot(range(len(faithfulness_trend)), faithfulness_trend, marker="o", color="#16a34a")
    ax.set_xticks(range(len(faithfulness_trend)))
    ax.set_xticklabels(faithfulness_labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylim(0.6, 1.0)
    ax.set_title("Faithfulness Across Fixes")
    for i, v in enumerate(faithfulness_trend):
        ax.text(i, v + 0.015, f"{v:.3f}", ha="center", fontsize=8)

    # Panel 3: Faithfulness vs Factual Correctness -- the groundedness/correctness gap
    ax = axes[2]
    labels = ["Faithfulness\n(vs. context)", "Factual Correctness\n(vs. reference)"]
    vals = [summary["faithfulness_avg"], summary["correctness_avg"]]
    colors = ["#16a34a", "#d97706"]
    bars = ax.bar(labels, vals, color=colors)
    ax.set_ylim(0, 1.05)
    ax.set_title("Groundedness vs. Correctness Gap")
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.02, f"{v:.3f}", ha="center", fontsize=8)

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out_path = ROOT / "assets" / "eval_results.png"
    fig.savefig(out_path, dpi=150)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
