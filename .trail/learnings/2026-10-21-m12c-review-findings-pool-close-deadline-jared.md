---
title: "M12c review findings: pool close, deadline bypasses, claim lease"
author: Jared
date: 2026-10-21
branch: fix/queue-visibility
touches:
  - config.py
  - app_factory.py
  - tests/test_config.py
  - backend/agentic_router.py
---

## Decided
- M12c (audit #3, #8-#12) went as four worktrees off one main SHA, merged A cache, B pg-pool, C chat-deadline, D queue; they auto-merge (config.py, app_factory.py, .env.example, tests/test_config.py are additive). Docs land on the last branch so main never documents unmerged settings.
- LLMDeadlineExceeded subclasses ChatClientError so check stages still degrade; only synthesis in backend/agentic_router.py re-raises it.
- The job claim is a CAS on job_id with a per-worker token; the SQS heartbeat renews the claim as well as the visibility.

## Tried and ruled out
- PRAGMA data_version for cross-process vector-cache freshness: per connection, and each search opens a new one. Triggers bump store_meta.vector_generation instead.
- Closing a pooled psycopg connection via the pool context's __exit__(None, None, None): that commits uncommitted work. Throw into the checkout context so the pool rolls back.
- requests iter_content(8192) for deadline-bounded LLM bodies: it blocks until 8192 bytes on a Content-Length body, so a byte-trickle never reaches the deadline check. Use urllib3 resp.raw.read1().

## Watch out
- Any new `except ChatClientError` around synthesis swallows the deadline and falls back to simulation; keep an `except LLMDeadlineExceeded: raise` first.
- ZIP64 locator offsets must be unpacked unsigned (<Q) and bounds-checked, or a crafted file raises OSError (500) out of check_container.
- Worker-written tests passed on all four buggy versions; each defect needed a new test proven to fail on the old code.
