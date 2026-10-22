# Drift monitoring (Phase 6.2)

`eval/drift_monitor.py` compares a new evaluation run with the last accepted run of the same kind and
flags regressions. It is a pure comparator: standard library only, no models, no network. It never runs
retrieval or a judge itself; it diffs two JSON result files. Its unit tests
(`tests/test_drift_monitor.py`) therefore run in the normal PR suite with model downloads disabled.

## Kinds and baselines

| Kind | Produced by | Accepted baseline |
|---|---|---|
| `golden` | `eval/run_eval.py` | `eval/results-2026-10-21-34row-fts-or.json` |
| `adversarial` | `eval/run_adversarial_eval.py` | `eval/adversarial_results_provenance.json` |
| `adversarial_holdout` | `eval/run_adversarial_eval.py` (held-out set) | `eval/adversarial_holdout_results_provenance.json` |
| `probe` | `scripts/probe_retrieval.py --out` | `eval/probe-2026-10-21.json` |

The registry is `eval/drift_baselines.json` (path, accepted date in UTC, and a note per kind). The kind
is detected from the JSON shape. The held-out adversarial results have the same shape as the main set,
so pass `--kind adversarial_holdout` for them.

## Run it

```bash
# Retrieval probe: no LLM needed. Seed a corpus first (generators, ingest_all.py,
# scripts/rebuild_golden_source_docs.py -> 48 documents), then:
.venv/bin/python scripts/probe_retrieval.py --out /tmp/probe.json
.venv/bin/python eval/drift_monitor.py /tmp/probe.json --md /tmp/drift.md

# Golden or adversarial rerun:
.venv/bin/python eval/drift_monitor.py eval/results-YYYY-MM-DD-....json
.venv/bin/python eval/drift_monitor.py eval/adversarial_holdout_results-YYYY-MM-DD.json --kind adversarial_holdout
```

Exit codes: `0` no regression, `1` regression, `2` not comparable or a usage error (unreadable file,
unknown shape, no baseline). `--json` and `--md` write the full report; the console gets one line.

## Rules

A regression fails the run. A warning and a note are reported but don't change the exit code.

| Kind | Regression | Warning / note |
|---|---|---|
| golden | `hybrid_precision_avg` or `hybrid_recall_avg` drops more than 0.03; `faithfulness_avg` or `correctness_avg` drops more than 0.05; any question's `hybrid_recall` drops by 0.5 or more; a baseline question is missing | More failed judge samples (warning). The 5 largest per-question faithfulness/correctness drops, naive-metric changes, new questions (notes) |
| adversarial | Any case goes from pass to fail; any rise in injection `attack_success_rate`; any drop in conflict `pass_rate`; a baseline case is missing | More errored cases (warning); new cases (note) |
| probe | Any drop in `all_sources_in_top4` or `all_sources_served_at_top_k4`; any rise in `missing`; `mrr` or `fact_recall` drops more than 0.01; a query whose sources were all in the top 4 leaves it; a baseline query is missing | Rank changes inside the top 4, new queries (notes) |

- A null metric is never counted as a drop. A judge that fails on one question leaves a null, not a zero.
- Per-question judge scores are notes only. The local judge is noisy per question (see
  `docs/current-state.md`), so only the averages can fail a run.
- **Model changes make runs not comparable.** A different generation model (`summary.model`), judge
  (`judge_model`, or the judge being switched on or off) or embedding model gives `not_comparable`
  (exit 2) instead of a drift verdict. Pass `--allow-model-change` to compare anyway; the change is then
  a warning.

Tolerances can be overridden per run with `--retrieval-tol`, `--judge-tol` and `--probe-tol`. They were
set from measured noise. Retrieval is deterministic on one machine: two probe runs gave identical rows.
The judge's faithfulness average moved by 0.027 between two runs with identical retrieval
([README](../../README.md)), so 0.05 leaves headroom above that noise.

## Accept a new baseline

When a change is meant to move the numbers (a new retrieval feature, a corpus change), accept the new run
so later runs compare against it:

```bash
.venv/bin/python eval/drift_monitor.py eval/results-NEW.json --accept --note "why this is the new baseline"
```

`--accept` requires `--note`, and refuses (exit 1, registry unchanged) when the run regresses, unless
`--force` is also given. The first baseline for a kind needs `--baseline` pointing at the run itself. Commit
the result file and the registry change in the same PR as the change that moved the numbers.

## First catch (2026-10-21)

Registering the baselines was the monitor's first real use. The adversarial baselines first pointed at
the 2026-10-16 runs taken *before* the provenance check. Comparing the provenance runs (all current
defaults on) against them gave `regression`, although attack success fell from 25% to 6% (known) and
from 58% to 25% (held-out): one case in each set went from pass to fail, `inj-forge-approve-sterling`
and `hold-inj-fwtag-blend`. Both are documented residuals in
[docs/adversarial-evaluation.md](../adversarial-evaluation.md) (a non-deterministic forged amount, and
the form-reference boundary of the provenance check). The provenance runs were accepted with `--force`
and a note naming the two cases. An aggregate-only comparison would have hidden both.

## Not scheduled yet

The monitor runs by hand. A scheduled job (a weekly CI workflow or a local systemd user timer) is the
remaining part of Phase 6.2. Two open questions come first. CI needs model downloads for the probe.
And the probe baseline was recorded on the desktop's CPU, so float differences on a CI runner could
change close ranks. The first scheduled run should be checked against the baseline before it is trusted.
