# Adversarial eval: checkpoint 2026-09-26

**Accepted by Claude in Astra role — Astra review pending.**

Branch `eval/adversarial-suite` (base `216901d`). Baseline suite committed `70bf617`; answer guard (fix 1) committed on top. Pre-existing edits to `CLAUDE.md`,
`docs/build-history.md` and the untracked `AGENTS.md` predate this work and are mixed into the same files.
Separate them when committing.

| Task | Executor | Session | Status |
|---|---|---|---|
| Contract, validator, review, live run, docs | Claude `claude-opus-5-5` (root) | claude-code:d7c1fbd9-136c-426b-a529-574eb8b600c3 | done |
| T1 attack corpus `eval/adversarial/cases.py` | Grok, launched `-m grok-4.6`; runtime `modelUsage` reports `grok-4.6-build` | grok session 01a0dcbd-8af9-7892-b099-8a68733af413 | ended `stopReason: cancelled` after writing the file; no self-check or report. Root validated it (38 fixtures, 27 cases, OK), rewrote 6 leading conflict queries, and accepted it. Cost $0.186 (`result.json`) |
| T2 harness (scoring, ingest, runner, tests) | Flash `deepseek/deepseek-v4.1-flash` | codex-exec:01a0dcbe-f290-7f93-8656-2558b655dd26 | owner-accepted. 3,290,484 input tokens (3,238,400 cached), 50,830 output, 224 s (`runs/T2/meta.json`) |

Post-acceptance root edits:
- `ingest.uvicorn_command` isolates the audit log and jobs DB and sets `SIMULATION_MODE=false`.
- `aggregate` gained `by_subtype` and `contaminated`.
- `foreign_hits` / `injection_markers` added. The runner records `foreign_hits`.

Grok worktree `/home/jaredf/Projects/jf1shh/auto-claims-rag-adv-cases` (branch `eval/adversarial-cases`, no
commits) holds the same `cases.py`. Safe to remove after the commit.

Resume: commit the suite on `eval/adversarial-suite`, then start the fix phase (see
`docs/adversarial-evaluation.md`, "What this points at").

## Fix 1: answer guard

| Task | Executor | Session | Status |
|---|---|---|---|
| T3 detectors `backend/answer_guard.py` | Flash | codex-exec:01a0dd1a-23f8-7ac0-9671-a342f6538a77 | owner-accepted. 8.11M input (8.05M cached), 64.9k output, 2935 s, mostly a sandbox ASGI hang unrelated to the repo |
| T4 bypass/false-positive review | Grok, `-m grok-4.6`, runtime `grok-4.6-build`, plan mode | 01a0dd47-751c-7753-9cd7-f6feb00a4357 | done, `end_turn`, $0.158. `runs/T4/review.md` |
| T3b consolidated correction | Flash, same session resumed | codex-exec:01a0dd1a-23f8-7ac0-9671-a342f6538a77 | owner-accepted. 16.34M input (16.13M cached), 125.7k output, 478 s. Root fixed B11 afterwards |
| Wiring (config, contract, API, audit, API tests), live reruns | Claude (root) | — | done |

Live: decision forgery 4/4 withheld, exfil 2/2, attack success 87.5%→56.3%, golden false positives 0/19.
Resume: fix 2 (emit `conflicting_evidence`), then fix 3 (prompt hardening for the 9 remaining classes).
