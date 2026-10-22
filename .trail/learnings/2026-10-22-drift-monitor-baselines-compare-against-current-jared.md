---
title: "Drift monitor baselines: compare against current-defaults runs"
author: Jared
date: 2026-10-22
branch: feat/drift-monitor
touches:
  - CONTEXT.md
  - docs/build-history.md
  - docs/current-state.md
  - docs/governance/naic-ai-bulletin-map.md
  - docs/operations/drift-monitoring.md
  - eval/drift_baselines.json
  - eval/drift_monitor.py
  - eval/probe-2026-10-21.json
  - tests/test_drift_monitor.py
  - ingest_all.py
---

## Decided
- eval/drift_monitor.py is a stdlib-only comparator; eval/drift_baselines.json holds one accepted run per kind (golden, adversarial, adversarial_holdout, probe). Model/judge/embedding change = not_comparable (exit 2), regression = exit 1.
- Adversarial baselines are the `_provenance` runs (all defaults on), not the older 10-16 runs. Per-question judge scores are notes only.
## Tried and ruled out
- Baselining against the pre-provenance runs: lower attack rates still hid two pass->fail cases (inj-forge-approve-sterling, hold-inj-fwtag-blend); both documented residuals, accepted with --force.
## Watch out
- The probe needs the 48-doc seed: generators, ingest_all.py, then scripts/rebuild_golden_source_docs.py (ingest alone = 35). Seed in a git-archive copy; the scripts write to cwd.
- First registration of a kind needs --baseline <same file> --accept.
