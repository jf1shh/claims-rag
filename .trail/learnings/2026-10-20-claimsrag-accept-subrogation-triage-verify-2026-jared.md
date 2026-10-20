---
title: "ClaimsRAG: accept subrogation triage, verify 2026-10-19 security mediums"
author: Jared
date: 2026-10-20
branch: fix/security-hardening
cost: { minutes: 2, tokens: 67631 }
touches:
  - backend/ingestion.py
  - backend/job_store.py
  - backend/rag_engine.py
  - ingest_all.py
  - docs/agent-work/security-review-2026-10-19.md
  - config.py
  - backend/postgres_store.py
  - app_factory.py
---

## Decided
- Accepted the routed Opus subro worker (commit 0ced9fc) after checking it against the brief and confirming every cited SOP section (rebuild_golden_source_docs.py ~219/~328) and dossier date (ingest_all.py) matched.
- All 6 medium findings in docs/agent-work/security-review-2026-10-19.md are real; none are false positives. APP_ENV defaults to development in config.py:177 and the Dockerfile sets none, so a plain `docker run` serves a no-credential admin.
- Split the fixes by file set, one writer at a time (all of them run the full suite): B storage (M2 cache gen+lock, M3 dedupe in-flight only, M5 generic conflict text, M6 embed before the write txn), A web edge (Origin check, TrustedHost in dev, Dockerfile APP_ENV=production, concurrency semaphores, plus lows L7/L8/L15/L17), C remaining lows.
- L11 .xls: drop it from the allowlist rather than add xlrd (a legacy binary parser running on untrusted files).
## Tried and ruled out
- Per-(claim, filename) uniqueness for M5: it's a schema change across filename-keyed routes, so a generic message is used instead. The accepted residual is that the message still confirms the name exists.
- WAL for M6: it adds -wal/-shm side files. Use a short transaction plus timeout=30 instead.
## Watch out
- backend/postgres_store.py:144 has the same claim-leaking conflict message; it's in brief C, not B.
- M3: an explicit idempotency_key must still return the old job whatever its status. Only the checksum key dedupes in-flight jobs only.
- Settings live in the repo-root config.py and app_factory.py, not backend/.
