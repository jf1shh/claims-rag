---
title: crag.py compact dev CLI
author: Jared
date: 2026-10-17
branch: feat/focus-devtools
cost: { minutes: 4, tokens: 88541 }
touches:
  - backend/harness.py
  - scripts/crag.py
  - tests/test_crag.py
---

## Decided
- scripts/crag.py wraps pytest + run_foundation_gates.py + eval JSON summaries + git/service status into short, fixed-size output (<=20 lines/subcommand) for agent token savings.
- Gate output parsing relies on backend/harness.py's format_finding(): "[error]"/"[warning] <rule_id>: msg (evidence)". Gate status line regexes: `foundation gate (passed|failed)`, `(\d+) finding`, `(\d+) blocking`. On a *failed* gate, the stderr line only has "N blocking finding(s)" (no total "N finding(s)"), so the findings count comes back as "?" by design — that's expected, not a bug.
- `ACV` keyword in coverage domain matching must be whole-word (`\bacv\b`) specifically to avoid false positives like "vacuum"; all other keywords are deliberately plain substring matches (e.g. "subrogat" matches "subrogation").
- `.venv/bin/python` is preferred for subprocesses when present, else `sys.executable`.

## Tried and ruled out
- Matching `cmd[1] == "pytest"` in a test's fake subprocess.run to distinguish the pytest call from the gate call — fails because the real argv is `[py, "-m", "pytest", ...]`, so index 1 is "-m". Use `"pytest" in cmd` instead.

## Watch out
- eval/adversarial_results*.json files can have missing aggregate sub-keys (e.g. no "injection" section) — code must `.get(..., "?")` at every nesting level, not just the top one.
- golden_guard_results.json's top-level "summary" dict is all scalars in the current fixture; the flattening logic filters by `isinstance(value, (int, float, str, bool, type(None)))` to stay safe if a future field becomes a nested object.
