---
title: "ClaimsRAG M12d audit lows: review and verify across two stacked branches"
author: Jared
date: 2026-10-21
branch: fix/m12d-storage
cost: { minutes: 17, tokens: 165973 }
touches:
  - SECURITY.md
  - alembic/versions/0003_force_row_level_security.py
  - backend/api.py
  - backend/blob_store.py
  - backend/job_store.py
  - backend/postgres_store.py
  - backend/rag_engine.py
  - docs/build-history.md
  - docs/current-state.md
  - docs/operations/local-and-production.md
---

## Decided
- Split the audit lows by file set: storage/privacy (#15-#18, #25) and runtime (#19-#21), both on feat/claim-purge. The M12d docs for both live on fix/m12d-storage, so merge it after fix/m12d-runtime.
- A sync delete always scrubs job rows, even when its own record_result failed. A job-store failure after a committed upload or delete returns success with job_id null.
- FORCE RLS goes in a new Alembic 0003 (never edit 0001/0002), plus a startup warning for superuser/BYPASSRLS roles. The warning fires in CI because CI connects as postgres; that's expected.

## Tried and ruled out
- Treating trail check's 20 hits as real: they're keyword matches on rag_engine.py lines these diffs don't touch (vector cache, per-claim filename). Explain them in the commit body.

## Watch out
- Branches share the crag-pg-verify database. Once a branch with migration 0003 has run, a branch without it fails with "Can't locate revision identified by '0003'". Recreate it: `docker exec crag-pg-verify psql -U postgres -c "DROP DATABASE rag WITH (FORCE)" -c "CREATE DATABASE rag"`.
- The m12d branches sit on a local octopus base (1f844f3) and can't be pushed until PRs #35-#38 merge. Then rebase with `--onto`.
