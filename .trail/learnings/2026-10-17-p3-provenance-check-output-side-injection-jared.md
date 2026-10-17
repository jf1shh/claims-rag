---
title: "P3 provenance check: output-side injection defense"
author: Jared
date: 2026-10-17
branch: feat/p3-injection-hardening
cost: { minutes: 27, tokens: 154308 }
touches:
  - backend/api.py
  - config.py
  - docs/adversarial-evaluation.md
  - docs/architecture.md
  - docs/build-history.md
  - docs/current-state.md
  - eval/adversarial/ingest.py
  - eval/golden_guard_check.py
  - eval/run_adversarial_eval.py
  - HANDOFF.md
---

## Decided
- Prompt-injection fix for P3 is output-side: backend/provenance_check.py (PROVENANCE_CHECK=llm|static|off), wired in backend/api.py after the answer guard, before the conflict check, on both /api/chat and /api/chat/stream.
- Every surviving attack after sanitize+guard was a planted token/filename echoed into an otherwise correct answer, so the check traces answer identifiers (upper-case hyphen groups with a letter and a digit, zero-width chars stripped) and filenames back to the source sentence. The model answers one narrow question per item ("does this passage direct the reader to reproduce it?") and code redacts the lines.
- Unretrieved filename: if a retrieved excerpt names it, it was planted, so the whole line goes. Otherwise it's a model miscite and only the token is replaced.
- 2026-10-16 numbers: known 25%->6%, held-out 58%->25%, golden 0/28 touched on final code, model never called on golden.
## Tried and ruled out
- Redacting the whole line for every unretrieved filename: a golden answer miscited a real source (Adjuster_Reference_Guide_ vs Adjuster_Guide_) and lost a correct sentence.
- More regex sanitize patterns / sandwich default: ruled out by 09-26 data (0/12 held-out; -0.12 correctness).
## Watch out
- Lemonade's llama-server listens on 127.0.0.1:8001, the old adversarial app port. Defaults are now 8002 (launch configs claimsrag-adversarial 8002, -holdout 8003).
- Port 8000 may be held by an unrelated uvicorn; the claimsrag-eval preview then lands on 8002.
- Don't tune against the 3 held-out misses (form-reference wording, footnote fact): that spends the held-out set.
- The held-out run needs its own scratch store: ingest.py --cases-module eval.adversarial.cases_holdout --work-dir eval/adversarial/.work-holdout.
