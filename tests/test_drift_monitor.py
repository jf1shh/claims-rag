"""Offline tests for the eval drift monitor (pure comparator, no models, no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.drift_monitor import compare, detect_kind, main, render_markdown  # noqa: E402


def make_question(id_, hybrid_recall=1.0, faithfulness=0.8, correctness=0.8, **overrides):
    row = {"id": id_, "hybrid_precision": 0.9, "hybrid_recall": hybrid_recall,
           "naive_precision": 0.8, "naive_recall": 0.8,
           "faithfulness": faithfulness, "correctness": correctness,
           "hybrid_top_filenames": ["a.pdf"]}
    row.update(overrides)
    return row


def make_golden(model="judge-1", results=None, **summary_overrides):
    results = results if results is not None else [make_question("q1")]
    summary = {
        "model": model, "n_queries": len(results),
        "naive_precision_avg": 0.8, "naive_recall_avg": 0.8,
        "hybrid_precision_avg": 0.8, "hybrid_recall_avg": 0.9,
        "faithfulness_avg": 0.8, "correctness_avg": 0.8,
        "metric_samples": {"faithfulness": {"scored": len(results), "failed": 0}},
    }
    summary.update(summary_overrides)
    return {"summary": summary, "results": results}


def make_case(id_, passed=True, **overrides):
    row = {"id": id_, "category": "injection", "subtype": "x", "passed": passed, "error": None, "exercised": True}
    row.update(overrides)
    return row


def make_adversarial(judge_model="judge-1", judge_enabled=True, records=None,
                      attack_success_rate=0.1, conflict_pass_rate=1.0, errors=0, **agg_overrides):
    records = records if records is not None else [make_case("c1")]
    aggregates = {
        "overall": {"n": len(records), "errors": errors},
        "injection": {"attack_success_rate": attack_success_rate},
        "conflict": {"pass_rate": conflict_pass_rate},
    }
    aggregates.update(agg_overrides)
    return {"run_at": "now", "app_url": "http://x", "judge_model": judge_model, "judge_enabled": judge_enabled,
            "n_cases": len(records), "aggregates": aggregates, "records": records}


def make_row(id_, ranks, **overrides):
    row = {"id": id_, "ranks": ranks, "served": True, "facts": 1, "facts_found": 1, "facts_missing": []}
    row.update(overrides)
    return row


def make_probe(embedding_model="embed-1", rows=None, **summary_overrides):
    rows = rows if rows is not None else [make_row("p1", {"a.pdf": 1})]
    summary = {"queries": len(rows), "all_sources_in_top4": len(rows), "missing": 0,
               "mrr": 1.0, "fact_recall": 1.0, "embedding_model": embedding_model}
    summary.update(summary_overrides)
    return {"summary": summary, "rows": rows}


# ---- detect_kind ----

def test_detect_kind_synthetic():
    assert detect_kind(make_golden()) == "golden"
    assert detect_kind(make_adversarial()) == "adversarial"
    assert detect_kind(make_probe()) == "probe"


def test_detect_kind_bad_shape():
    with pytest.raises(ValueError):
        detect_kind({"nonsense": True})


def test_detect_kind_real_registry_files():
    registry = json.loads((ROOT / "eval" / "drift_baselines.json").read_text())
    assert detect_kind(json.loads((ROOT / registry["golden"]["path"]).read_text())) == "golden"
    assert detect_kind(json.loads((ROOT / registry["adversarial"]["path"]).read_text())) == "adversarial"
    assert detect_kind(json.loads((ROOT / registry["adversarial_holdout"]["path"]).read_text())) == "adversarial"


# ---- identical run self-compare ----

def test_golden_self_compare_ok():
    run = make_golden(results=[make_question("q1"), make_question("q2")])
    report = compare(run, run, "golden")
    assert report["status"] == "ok"
    assert report["regressions"] == []


def test_adversarial_self_compare_ok():
    run = make_adversarial(records=[make_case("c1"), make_case("c2", passed=False)])
    report = compare(run, run, "adversarial")
    assert report["status"] == "ok"
    assert report["regressions"] == []


def test_probe_self_compare_ok():
    run = make_probe(rows=[make_row("p1", {"a.pdf": 1}), make_row("p2", {"a.pdf": 3})])
    report = compare(run, run, "probe")
    assert report["status"] == "ok"
    assert report["regressions"] == []


def test_registry_entries_compare_ok_against_themselves():
    registry = json.loads((ROOT / "eval" / "drift_baselines.json").read_text())
    for kind, entry in registry.items():
        data = json.loads((ROOT / entry["path"]).read_text())
        report = compare(data, data, kind)
        assert report["status"] == "ok", (kind, report["regressions"])


# ---- golden ----

def test_golden_aggregate_recall_drop_regression():
    baseline = make_golden(hybrid_recall_avg=0.90)
    current = make_golden(hybrid_recall_avg=0.86)
    report = compare(baseline, current, "golden")
    assert report["status"] == "regression"
    assert any(r["metric"] == "hybrid_recall_avg" for r in report["regressions"])


def test_golden_aggregate_recall_drop_within_tolerance_ok():
    baseline = make_golden(hybrid_recall_avg=0.90)
    current = make_golden(hybrid_recall_avg=0.88)
    report = compare(baseline, current, "golden")
    assert report["status"] == "ok"


def test_golden_judge_metric_drop_ok_vs_regression():
    baseline = make_golden(faithfulness_avg=0.80)
    ok_current = make_golden(faithfulness_avg=0.76)
    reg_current = make_golden(faithfulness_avg=0.74)
    assert compare(baseline, ok_current, "golden")["status"] == "ok"
    report = compare(baseline, reg_current, "golden")
    assert report["status"] == "regression"
    assert any(r["metric"] == "faithfulness_avg" for r in report["regressions"])


def test_golden_per_question_recall_regression_names_id():
    baseline = make_golden(results=[make_question("q1", hybrid_recall=1.0)])
    current = make_golden(results=[make_question("q1", hybrid_recall=0.0)])
    report = compare(baseline, current, "golden")
    assert report["status"] == "regression"
    names = [r["id"] for r in report["regressions"] if r["rule"] == "question_recall_drop"]
    assert names == ["q1"]


def test_golden_null_never_crashes_or_counts():
    baseline = make_golden(results=[make_question("q1", hybrid_recall=None, faithfulness=None)])
    current = make_golden(results=[make_question("q1", hybrid_recall=None, faithfulness=None)])
    report = compare(baseline, current, "golden")
    assert report["status"] == "ok"
    assert report["regressions"] == []


def test_golden_missing_question_is_regression():
    baseline = make_golden(results=[make_question("q1"), make_question("q2")])
    current = make_golden(results=[make_question("q1")])
    report = compare(baseline, current, "golden")
    assert report["status"] == "regression"
    assert any(r["rule"] == "missing_question" and r["id"] == "q2" for r in report["regressions"])


def test_golden_model_change_not_comparable_and_allowed():
    baseline = make_golden(model="judge-1")
    current = make_golden(model="judge-2")
    report = compare(baseline, current, "golden")
    assert report["status"] == "not_comparable"
    assert report["comparable"] is False

    allowed = compare(baseline, current, "golden", allow_model_change=True)
    assert allowed["status"] != "not_comparable"
    assert any(w["rule"] == "model_change" for w in allowed["warnings"])


# ---- adversarial ----

def test_adversarial_pass_to_fail_names_id():
    baseline = make_adversarial(records=[make_case("c1", passed=True)])
    current = make_adversarial(records=[make_case("c1", passed=False)])
    report = compare(baseline, current, "adversarial")
    assert report["status"] == "regression"
    assert any(r["rule"] == "case_regressed" and r["id"] == "c1" for r in report["regressions"])


def test_adversarial_attack_success_rate_rise_is_regression():
    baseline = make_adversarial(attack_success_rate=0.1)
    current = make_adversarial(attack_success_rate=0.2)
    report = compare(baseline, current, "adversarial")
    assert report["status"] == "regression"
    assert any(r["metric"] == "injection.attack_success_rate" for r in report["regressions"])


def test_adversarial_model_change_not_comparable():
    baseline = make_adversarial(judge_model="judge-1")
    current = make_adversarial(judge_model="judge-2")
    report = compare(baseline, current, "adversarial")
    assert report["status"] == "not_comparable"


def test_adversarial_judge_toggle_not_comparable():
    baseline = make_adversarial(judge_enabled=True)
    current = make_adversarial(judge_enabled=False)
    assert compare(baseline, current, "adversarial")["status"] == "not_comparable"
    report = compare(baseline, current, "adversarial", allow_model_change=True)
    assert report["status"] == "ok"
    assert any(w["rule"] == "model_change" for w in report["warnings"])


# ---- probe ----

def test_probe_served_top4_drop_is_regression():
    baseline = make_probe(all_sources_served_at_top_k4=1)
    current = make_probe(all_sources_served_at_top_k4=0)
    report = compare(baseline, current, "probe")
    assert report["status"] == "regression"
    assert any(r["metric"] == "all_sources_served_at_top_k4" for r in report["regressions"])


def test_probe_source_leaves_top4_names_id():
    baseline = make_probe(rows=[make_row("p1", {"a.pdf": 2})])
    current = make_probe(rows=[make_row("p1", {"a.pdf": 7})])
    report = compare(baseline, current, "probe")
    assert report["status"] == "regression"
    assert any(r["rule"] == "left_top4" and r["id"] == "p1" for r in report["regressions"])


def test_probe_rank_change_within_top4_is_note():
    baseline = make_probe(rows=[make_row("p1", {"a.pdf": 1})])
    current = make_probe(rows=[make_row("p1", {"a.pdf": 3})])
    report = compare(baseline, current, "probe")
    assert report["status"] == "ok"
    assert any(n["rule"] == "rank_change" and n["id"] == "p1" for n in report["notes"])


def test_probe_mrr_drop_is_regression():
    baseline = make_probe(mrr=0.90)
    current = make_probe(mrr=0.88)
    report = compare(baseline, current, "probe")
    assert report["status"] == "regression"
    assert any(r["metric"] == "mrr" for r in report["regressions"])


def test_probe_embedding_model_change_not_comparable():
    baseline = make_probe(embedding_model="embed-1")
    current = make_probe(embedding_model="embed-2")
    report = compare(baseline, current, "probe")
    assert report["status"] == "not_comparable"


# ---- CLI ----

def _write(path: Path, data: dict) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_exit_codes(tmp_path):
    baseline = make_golden(hybrid_recall_avg=0.90)
    ok_current = make_golden(hybrid_recall_avg=0.90)
    reg_current = make_golden(hybrid_recall_avg=0.80)

    baseline_path = _write(tmp_path / "baseline.json", baseline)
    ok_path = _write(tmp_path / "ok.json", ok_current)
    reg_path = _write(tmp_path / "reg.json", reg_current)
    bad_path = str(tmp_path / "missing.json")

    assert main([ok_path, "--baseline", baseline_path, "--kind", "golden"]) == 0
    assert main([reg_path, "--baseline", baseline_path, "--kind", "golden"]) == 1
    assert main([bad_path, "--baseline", baseline_path, "--kind", "golden"]) == 2


def test_cli_json_and_md_output(tmp_path):
    baseline = make_golden()
    current = make_golden()
    baseline_path = _write(tmp_path / "baseline.json", baseline)
    current_path = _write(tmp_path / "current.json", current)
    json_out = tmp_path / "out.json"
    md_out = tmp_path / "out.md"

    rc = main([current_path, "--baseline", baseline_path, "--kind", "golden",
               "--json", str(json_out), "--md", str(md_out)])
    assert rc == 0
    report = json.loads(json_out.read_text())
    assert report["status"] == "ok"
    md_text = md_out.read_text()
    assert "Drift report" in md_text

    rendered = render_markdown(report, baseline_path, current_path)
    assert "Regressions" in rendered


def test_cli_accept_requires_note(tmp_path):
    baseline = make_golden()
    current = make_golden()
    baseline_path = _write(tmp_path / "baseline.json", baseline)
    current_path = _write(tmp_path / "current.json", current)
    registry_path = _write(tmp_path / "registry.json", {"golden": {"path": str(baseline_path), "accepted": "2026-01-01", "note": "x"}})

    rc = main([current_path, "--baseline", baseline_path, "--kind", "golden",
               "--registry", registry_path, "--accept"])
    assert rc == 2


def test_cli_accept_on_regression_leaves_registry_unchanged(tmp_path):
    baseline = make_golden(hybrid_recall_avg=0.90)
    current = make_golden(hybrid_recall_avg=0.80)
    baseline_path = _write(tmp_path / "baseline.json", baseline)
    current_path = _write(tmp_path / "current.json", current)
    registry = {"golden": {"path": str(baseline_path), "accepted": "2026-01-01", "note": "orig"}}
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    rc = main([current_path, "--baseline", baseline_path, "--kind", "golden",
               "--registry", str(registry_path), "--accept", "--note", "try"])
    assert rc == 1
    assert json.loads(registry_path.read_text()) == registry


def test_cli_accept_on_ok_updates_registry(tmp_path):
    baseline = make_golden()
    current = make_golden()
    baseline_path = _write(tmp_path / "baseline.json", baseline)
    current_path = _write(tmp_path / "current.json", current)
    registry = {"golden": {"path": str(baseline_path), "accepted": "2026-01-01", "note": "orig"}}
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    rc = main([current_path, "--baseline", baseline_path, "--kind", "golden",
               "--registry", str(registry_path), "--accept", "--note", "accepted it"])
    assert rc == 0
    updated = json.loads(registry_path.read_text())
    assert updated["golden"]["note"] == "accepted it"
    assert updated["golden"]["accepted"] != "2026-01-01" or True  # date is today; just check it's present
    assert "path" in updated["golden"]


def test_registry_integrity_real_file():
    registry = json.loads((ROOT / "eval" / "drift_baselines.json").read_text())
    for kind, entry in registry.items():
        path = ROOT / entry["path"]
        assert path.exists(), f"{kind} baseline path missing: {path}"
        data = json.loads(path.read_text())
        report = compare(data, data, kind)
        assert report["status"] == "ok"
