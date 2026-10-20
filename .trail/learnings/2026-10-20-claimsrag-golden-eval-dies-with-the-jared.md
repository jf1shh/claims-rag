---
title: ClaimsRAG golden eval dies with the session; relaunch detached
author: Jared
date: 2026-10-20
branch: fix/security-hardening
touches:
  - eval/run_eval.py
  - eval/results.json
---

## Decided
- Launch long evals (`eval/run_eval.py`) with `setsid nohup .venv/bin/python -u ... < /dev/null & disown` so they survive a claude-loop handoff, and use `-u` so the log shows per-row progress (`[n/34] <id>`).
- Eval targets the port-8010 uvicorn (`EVAL_APP_URL=http://127.0.0.1:8010`); port 8000 is the Nexus backend.

## Tried and ruled out
- Plain background launch from the session: the eval process died at handoff after 32/34 chats and wrote nothing (results.json is written only at the end). The uvicorn server and the nexus-route supervisor survived.

## Watch out
- `eval/results.json` is written only after all rows finish, so a killed run loses everything; check `pgrep -af run_eval` plus the results.json mtime, not the handoff's pid.
- Foreground `sleep N; cmd` is blocked by the harness; wait with background until-loops instead.
