---
title: Stacked PR retarget does not trigger claims-rag CI
author: Jared
date: 2026-10-22
branch: docs/drift-monitor-merged
touches:
  - docs/current-state.md
---

## Decided
- When a base PR merges, GitHub retargets the stacked PR to main. That is a pull_request `edited` event, and tests.yml (pull_request into main, default types) does not run on it. Merge origin/main into the stacked branch (no force-push) and push; the `synchronize` event starts CI. Used for #45 on 2026-10-21.

## Tried and ruled out
- `gh pr checks N --watch` right after the push exits 0 with "no checks reported" while the run is queued. Watch the run instead: `gh run watch <databaseId>` from `gh run list --branch B --limit 1`.
- Merging origin/main from a fetch taken before `gh pr merge` gives "Already up to date". Fetch again after the merge.

## Watch out
- `git branch -d` on a merged branch fails from the main checkout, which sits on the stale fix/security-hardening. Check with `git merge-base --is-ancestor <sha> origin/main` instead.
