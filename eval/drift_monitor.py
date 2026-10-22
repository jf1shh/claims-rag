"""Compare a new eval run against the last accepted baseline of the same kind and flag regressions.

A pure comparator (stdlib only): never runs retrieval or a judge, just diffs two JSON result
files. Supports three kinds, auto-detected from the JSON shape: "golden" (eval/run_eval.py),
"adversarial" / "adversarial_holdout" (eval/run_adversarial_eval.py), and "probe"
(scripts/probe_retrieval.py --out).

Usage:
    .venv/bin/python eval/drift_monitor.py CURRENT.json [--baseline PATH] [--kind KIND]
        [--registry eval/drift_baselines.json] [--json OUT] [--md OUT]
        [--allow-model-change] [--retrieval-tol F] [--judge-tol F] [--probe-tol F]
        [--accept --note "why"] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RETRIEVAL_TOL = 0.03
JUDGE_TOL = 0.05
PROBE_TOL = 0.01
ATTACK_TOL = 0.0
GOLDEN_RECALL_DROP = 0.5
JUDGE_NOTE_TOP_N = 5


def detect_kind(data: dict) -> str:
    if "aggregates" in data:
        return "adversarial"
    if "results" in data and "summary" in data:
        return "golden"
    if "rows" in data and isinstance(data.get("summary"), dict) and "mrr" in data["summary"]:
        return "probe"
    raise ValueError(
        "cannot detect kind: expected 'aggregates' (adversarial), 'results'+'summary' (golden), "
        "or 'rows'+'summary.mrr' (probe)"
    )


def _finding(rule: str, metric: str, id_, baseline, current) -> dict:
    delta = current - baseline if isinstance(baseline, (int, float)) and isinstance(current, (int, float)) else None
    return {"rule": rule, "metric": metric, "id": id_, "baseline": baseline, "current": current, "delta": delta}


def _new_report(kind: str) -> dict:
    return {"kind": kind, "status": "ok", "comparable": True, "regressions": [], "warnings": [], "notes": [], "summary": {}}


def _track_aggregate(report: dict, metric: str, baseline, current) -> tuple | None:
    if not isinstance(baseline, (int, float)) or not isinstance(current, (int, float)):
        return None
    delta = current - baseline
    report["summary"][metric] = {"baseline": baseline, "current": current, "delta": delta}
    return baseline, current, delta


def _compare_golden(baseline: dict, current: dict, tol: dict, allow_model_change: bool) -> dict:
    report = _new_report("golden")
    b_summary = baseline.get("summary") or {}
    c_summary = current.get("summary") or {}

    b_model, c_model = b_summary.get("model"), c_summary.get("model")
    if b_model != c_model:
        finding = _finding("model_change", "model", None, b_model, c_model)
        if allow_model_change:
            report["warnings"].append(finding)
        else:
            report["comparable"] = False
            report["status"] = "not_comparable"
            return report

    for metric, rule_tol in (("hybrid_precision_avg", tol["retrieval_tol"]), ("hybrid_recall_avg", tol["retrieval_tol"]),
                              ("faithfulness_avg", tol["judge_tol"]), ("correctness_avg", tol["judge_tol"])):
        tracked = _track_aggregate(report, metric, b_summary.get(metric), c_summary.get(metric))
        if tracked and (tracked[0] - tracked[1]) > rule_tol:
            report["regressions"].append(_finding("aggregate_drop", metric, None, tracked[0], tracked[1]))

    for metric in ("naive_precision_avg", "naive_recall_avg"):
        tracked = _track_aggregate(report, metric, b_summary.get(metric), c_summary.get(metric))
        if tracked and tracked[2] != 0:
            report["notes"].append(_finding("naive_metric_change", metric, None, tracked[0], tracked[1]))

    b_results = {r["id"]: r for r in baseline.get("results") or []}
    c_results = {r["id"]: r for r in current.get("results") or []}

    judge_drops = []
    for qid, b_row in b_results.items():
        c_row = c_results.get(qid)
        if c_row is None:
            report["regressions"].append(_finding("missing_question", "id", qid, "present", "missing"))
            continue
        b_recall, c_recall = b_row.get("hybrid_recall"), c_row.get("hybrid_recall")
        if isinstance(b_recall, (int, float)) and isinstance(c_recall, (int, float)):
            if (b_recall - c_recall) >= GOLDEN_RECALL_DROP:
                report["regressions"].append(_finding("question_recall_drop", "hybrid_recall", qid, b_recall, c_recall))
        for metric in ("faithfulness", "correctness"):
            b_v, c_v = b_row.get(metric), c_row.get(metric)
            if isinstance(b_v, (int, float)) and isinstance(c_v, (int, float)):
                judge_drops.append((b_v - c_v, metric, qid, b_v, c_v))

    for qid in c_results:
        if qid not in b_results:
            report["notes"].append(_finding("new_question", "id", qid, None, None))

    judge_drops.sort(key=lambda row: -row[0])
    for _, metric, qid, b_v, c_v in judge_drops[:JUDGE_NOTE_TOP_N]:
        report["notes"].append(_finding("judge_metric_drop", metric, qid, b_v, c_v))

    b_samples = b_summary.get("metric_samples") or {}
    c_samples = c_summary.get("metric_samples") or {}
    for metric, b_stat in b_samples.items():
        c_stat = c_samples.get(metric)
        if not c_stat:
            continue
        b_failed, c_failed = b_stat.get("failed", 0), c_stat.get("failed", 0)
        if c_failed > b_failed:
            report["warnings"].append(_finding("failed_samples_increase", metric, None, b_failed, c_failed))

    report["status"] = "regression" if report["regressions"] else "ok"
    return report


def _compare_adversarial(baseline: dict, current: dict, kind: str, allow_model_change: bool) -> dict:
    report = _new_report(kind)

    b_judge_enabled, c_judge_enabled = baseline.get("judge_enabled"), current.get("judge_enabled")
    b_judge_model, c_judge_model = baseline.get("judge_model"), current.get("judge_model")
    # Turning the judge on or off changes what "passed" means, so it counts as a model change too.
    if b_judge_enabled != c_judge_enabled or (b_judge_enabled and b_judge_model != c_judge_model):
        finding = _finding("model_change", "judge_model", None,
                           b_judge_model if b_judge_enabled else None, c_judge_model if c_judge_enabled else None)
        if allow_model_change:
            report["warnings"].append(finding)
        else:
            report["comparable"] = False
            report["status"] = "not_comparable"
            return report

    b_records = {r["id"]: r for r in baseline.get("records") or []}
    c_records = {r["id"]: r for r in current.get("records") or []}

    for cid, b_row in b_records.items():
        c_row = c_records.get(cid)
        if c_row is None:
            report["regressions"].append(_finding("missing_case", "id", cid, "present", "missing"))
            continue
        if b_row.get("passed") is True and c_row.get("passed") is False:
            report["regressions"].append(_finding("case_regressed", "passed", cid, True, False))

    for cid in c_records:
        if cid not in b_records:
            report["notes"].append(_finding("new_case", "id", cid, None, None))

    b_agg, c_agg = baseline.get("aggregates") or {}, current.get("aggregates") or {}

    b_asr = (b_agg.get("injection") or {}).get("attack_success_rate")
    c_asr = (c_agg.get("injection") or {}).get("attack_success_rate")
    tracked = _track_aggregate(report, "injection.attack_success_rate", b_asr, c_asr)
    if tracked and tracked[2] > ATTACK_TOL:
        report["regressions"].append(_finding("attack_success_rate_rise", "injection.attack_success_rate", None, tracked[0], tracked[1]))

    b_conflict_pr = (b_agg.get("conflict") or {}).get("pass_rate")
    c_conflict_pr = (c_agg.get("conflict") or {}).get("pass_rate")
    tracked = _track_aggregate(report, "conflict.pass_rate", b_conflict_pr, c_conflict_pr)
    if tracked and (tracked[0] - tracked[1]) > 0.0:
        report["regressions"].append(_finding("conflict_pass_rate_drop", "conflict.pass_rate", None, tracked[0], tracked[1]))

    b_errors = (b_agg.get("overall") or {}).get("errors")
    c_errors = (c_agg.get("overall") or {}).get("errors")
    if isinstance(b_errors, (int, float)) and isinstance(c_errors, (int, float)) and c_errors > b_errors:
        report["warnings"].append(_finding("error_count_increase", "overall.errors", None, b_errors, c_errors))

    report["status"] = "regression" if report["regressions"] else "ok"
    return report


def _worst_rank(row: dict):
    ranks = (row.get("ranks") or {}).values()
    if not ranks:
        return None
    if any(v is None for v in ranks):
        return None
    return max(ranks)


def _rank_sort_key(worst):
    return worst if worst is not None else float("inf")


def _compare_probe(baseline: dict, current: dict, tol: dict) -> dict:
    report = _new_report("probe")
    b_summary = baseline.get("summary") or {}
    c_summary = current.get("summary") or {}

    if b_summary.get("embedding_model") != c_summary.get("embedding_model"):
        report["comparable"] = False
        report["status"] = "not_comparable"
        return report

    for metric, higher_is_better in (("all_sources_in_top4", True), ("all_sources_served_at_top_k4", True),
                                     ("missing", False)):
        tracked = _track_aggregate(report, metric, b_summary.get(metric), c_summary.get(metric))
        if not tracked:
            continue
        _, _, delta = tracked
        if higher_is_better and delta < 0:
            report["regressions"].append(_finding(f"{metric}_decrease", metric, None, tracked[0], tracked[1]))
        elif not higher_is_better and delta > 0:
            report["regressions"].append(_finding(f"{metric}_increase", metric, None, tracked[0], tracked[1]))

    for metric in ("mrr", "fact_recall"):
        tracked = _track_aggregate(report, metric, b_summary.get(metric), c_summary.get(metric))
        if tracked and (tracked[0] - tracked[1]) > tol["probe_tol"]:
            report["regressions"].append(_finding(f"{metric}_drop", metric, None, tracked[0], tracked[1]))

    b_rows = {r["id"]: r for r in baseline.get("rows") or []}
    c_rows = {r["id"]: r for r in current.get("rows") or []}

    for qid, b_row in b_rows.items():
        c_row = c_rows.get(qid)
        if c_row is None:
            report["regressions"].append(_finding("missing_query", "id", qid, "present", "missing"))
            continue
        b_worst, c_worst = _worst_rank(b_row), _worst_rank(c_row)
        b_key, c_key = _rank_sort_key(b_worst), _rank_sort_key(c_worst)
        if b_key <= 4 and c_key > 4:
            report["regressions"].append(_finding("left_top4", "worst_rank", qid, b_worst, c_worst))
        elif b_worst != c_worst:
            report["notes"].append(_finding("rank_change", "worst_rank", qid, b_worst, c_worst))

    for qid in c_rows:
        if qid not in b_rows:
            report["notes"].append(_finding("new_query", "id", qid, None, None))

    report["status"] = "regression" if report["regressions"] else "ok"
    return report


def compare(baseline: dict, current: dict, kind: str, *, tolerances: dict | None = None, allow_model_change: bool = False) -> dict:
    tol = {"retrieval_tol": RETRIEVAL_TOL, "judge_tol": JUDGE_TOL, "probe_tol": PROBE_TOL}
    if tolerances:
        tol.update(tolerances)

    if kind == "golden":
        return _compare_golden(baseline, current, tol, allow_model_change)
    if kind in ("adversarial", "adversarial_holdout"):
        return _compare_adversarial(baseline, current, kind, allow_model_change)
    if kind == "probe":
        return _compare_probe(baseline, current, tol)
    raise ValueError(f"unknown kind: {kind}")


def _render_section(lines: list[str], title: str, items: list[dict]) -> None:
    lines.append(f"## {title} ({len(items)})")
    if not items:
        lines.append("- none")
    for item in items:
        tag = f" [{item['id']}]" if item["id"] is not None else ""
        lines.append(f"- `{item['rule']}` {item['metric']}{tag}: {item['baseline']} -> {item['current']} (delta {item['delta']})")
    lines.append("")


def render_markdown(report: dict, baseline_path: str, current_path: str) -> str:
    lines = [
        f"# Drift report: {report['kind']}",
        "",
        f"Status: **{report['status']}**",
        f"Baseline: `{baseline_path}`",
        f"Current: `{current_path}`",
        "",
    ]
    _render_section(lines, "Regressions", report["regressions"])
    _render_section(lines, "Warnings", report["warnings"])
    _render_section(lines, "Notes", report["notes"])
    if report["summary"]:
        lines.append("## Summary metrics")
        for metric, vals in report["summary"].items():
            lines.append(f"- {metric}: {vals['baseline']} -> {vals['current']} (delta {vals['delta']})")
        lines.append("")
    return "\n".join(lines)


def _load_json(path: str | Path, label: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {label} {path}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("current")
    parser.add_argument("--baseline")
    parser.add_argument("--kind", choices=["golden", "adversarial", "adversarial_holdout", "probe"])
    parser.add_argument("--registry", default=str(REPO_ROOT / "eval" / "drift_baselines.json"))
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--md")
    parser.add_argument("--allow-model-change", action="store_true")
    parser.add_argument("--retrieval-tol", type=float)
    parser.add_argument("--judge-tol", type=float)
    parser.add_argument("--probe-tol", type=float)
    parser.add_argument("--accept", action="store_true")
    parser.add_argument("--note")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    current = _load_json(args.current, "current run")
    if current is None:
        return 2

    kind = args.kind
    if kind is None:
        try:
            kind = detect_kind(current)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    registry_path = Path(args.registry)
    if not registry_path.is_absolute():
        registry_path = REPO_ROOT / registry_path
    registry = _load_json(registry_path, "registry")
    if registry is None:
        return 2

    baseline_path = args.baseline
    if baseline_path is None:
        entry = registry.get(kind)
        if entry is None:
            print(f"no registry entry for kind '{kind}'; pass --baseline", file=sys.stderr)
            return 2
        baseline_path = str(REPO_ROOT / entry["path"])

    baseline = _load_json(baseline_path, "baseline")
    if baseline is None:
        return 2

    tolerances = {}
    if args.retrieval_tol is not None:
        tolerances["retrieval_tol"] = args.retrieval_tol
    if args.judge_tol is not None:
        tolerances["judge_tol"] = args.judge_tol
    if args.probe_tol is not None:
        tolerances["probe_tol"] = args.probe_tol

    report = compare(baseline, current, kind, tolerances=tolerances, allow_model_change=args.allow_model_change)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.md:
        Path(args.md).write_text(render_markdown(report, baseline_path, args.current) + "\n", encoding="utf-8")

    print(f"drift: {kind} {report['status']} — {len(report['regressions'])} regressions, "
          f"{len(report['warnings'])} warnings (baseline {baseline_path})")

    if args.accept:
        if not args.note:
            print("--accept requires --note", file=sys.stderr)
            return 2
        if report["status"] != "ok" and not args.force:
            print(f"refusing to accept: status is {report['status']} (use --force to override)", file=sys.stderr)
            return 1
        try:
            current_repo_path = str(Path(args.current).resolve().relative_to(REPO_ROOT))
        except ValueError:
            current_repo_path = args.current
        registry[kind] = {
            "path": current_repo_path,
            "accepted": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "note": args.note,
        }
        registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if report["status"] == "not_comparable":
        return 2
    if report["status"] == "regression":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
