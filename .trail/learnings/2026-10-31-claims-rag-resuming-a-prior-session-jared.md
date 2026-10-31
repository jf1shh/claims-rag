---
title: "claims-rag: resuming a prior-session worktree feature and verifying which module pytest actually imports"
author: Jared
date: 2026-10-31
branch: main
cost: { minutes: 5, tokens: 134398 }
touches:
  - ".eval-run/eval-20261025-jag-gate-on.log"
  - ".eval-run/server-20261025.log"
  - dirlink-to-filelinks.sh
  - docs/architecture.md
  - docs/current-state.md
  - CLAUDE.md
---

## Decided
- When a handoff says "python module X was modified in a worktree but we're not 100% sure pytest
  picked up the worktree's copy vs. the main checkout's copy," resolve it directly and cheaply:
  `cd <worktree> && <main-venv-python> -c "import backend.module as m; print(m.__file__)"`. Running
  from inside the worktree directory causes cwd to prepend to sys.path, so the worktree's copy
  wins even when invoking the main checkout's venv python by absolute path. Confirmed this
  explicitly before trusting an earlier "5 passed" test result as real evidence the new feature
  code was correct, rather than assuming it from the plausibility argument (a TypeError would
  likely have fired otherwise) alone.
- Commit documentation updates (architecture.md, current-state.md, build-history.md, a
  backend-constraints.md rule) in the SAME commit as the feature code they describe, not a
  separate docs-only commit, when a project's CLAUDE.md treats doc updates as part of closing out
  a milestone. Splitting them leaves a commit where code exists but mandatory docs don't.

## Tried and ruled out
- Did not re-litigate or re-verify the ABI-crash fix (Python 3.14 vs 3.13, uv pip installing
  torch+torchvision together) from the prior session -- it was already fixed and empirically
  confirmed; redoing that work would have been pure waste. Trust a handoff's "Done, verified"
  section unless something concrete contradicts it.

## Watch out
- A `run_in_background` Bash command (e.g. polling `gh pr checks <N>` in a loop until non-pending)
  does NOT survive a context-limit handoff to a fresh session -- its notification is scoped to the
  session that started it. If a handoff is likely before the background command finishes, prefer
  a short synchronous poll (a few `sleep 60-90` + check cycles) over `run_in_background` for waits
  expected to resolve in a few minutes, so the next session isn't left not knowing CI's outcome at
  all. The next-session handoff has to say "check CI fresh, don't wait on the dead background job."
- `.claude/worktrees/<name>/` has no `.venv` of its own on this machine -- every python invocation
  against worktree files must go through the main checkout's `.venv/bin/python` by absolute path.
