STATUS: **ready_for_review** — T2, workspace `/home/jaredf/Projects/jf1shh/auto-claims-rag` (branch `eval/adversarial-suite`), report at [report.md](/home/jaredf/Projects/jf1shh/auto-claims-rag/docs/agent-work/adversarial-eval/runs/T2/report.md).

**Changed files**
- [eval/adversarial/__init__.py](/home/jaredf/Projects/jf1shh/auto-claims-rag/eval/adversarial/__init__.py) (empty), [scoring.py](/home/jaredf/Projects/jf1shh/auto-claims-rag/eval/adversarial/scoring.py:58), [ingest.py](/home/jaredf/Projects/jf1shh/auto-claims-rag/eval/adversarial/ingest.py:77), [run_adversarial_eval.py](/home/jaredf/Projects/jf1shh/auto-claims-rag/eval/run_adversarial_eval.py:160), [test_adversarial_scoring.py](/home/jaredf/Projects/jf1shh/auto-claims-rag/tests/test_adversarial_scoring.py:332), one `.gitignore` line, plus the report. `cases.py`, `validate.py`, backend, CI and the live store were not touched; `CLAUDE.md` / `docs/build-history.md` / `AGENTS.md` were already dirty and remain untouched.

**Verification**
- `.venv/bin/pytest tests/test_adversarial_scoring.py -q` → exit 0, `15 passed in 0.01s`
- `.venv/bin/ruff check eval/adversarial eval/run_adversarial_eval.py tests/test_adversarial_scoring.py` → exit 0, `All checks passed!`
- import-check one-liner from the brief → exit 0, `imports ok`
- both `--help` commands → exit 0

Extra offline evidence beyond the brief (no server, no model): a stubbed-`requests` smoke of `main()` ran five runner scenarios (two-case run, ` yes` → `judge_ok=True`, `maybe` → `None`, transport error → `exercised/passed=False` with the exact error string, unknown `--only` → warning + `n_cases=0`) and confirmed the `/api/chat` payload and judge payload templates; the ingest guards exited 2 for scratch==source and a missing source, removed stale `-wal`/`-shm`/`stored_documents` on rerun, copied rows into the scratch DB while leaving the source unchanged, and stopped exactly at the expected `ModuleNotFoundError: eval.adversarial.cases` (T1's file). Also `_fixture_text` byte format and the `scripts.rebuild_golden_source_docs` import were checked directly.

**Not verified** (by design): the real indexing loop against T1's `cases.py` with the embedding model, and any live `/api/chat` or judge call.

**Risks / decisions for you**: `judge_raw` stores the reply as returned (pre-``-strip); an `--only` id that matches nothing warns and continues; judge timeout is 180s (unspecified in the brief); the read-only source URI assumes a POSIX path without `?`/`#`. Full detail in the report.