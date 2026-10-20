---
title: "ClaimsRAG resume: PR #24 green, security review launched"
author: Jared
date: 2026-10-20
branch: fix/security-hardening
---

## Decided
- Resumed from ~/.handoff/loop-1633722/HANDOFF.md. PR #24 (jf1shh/claims-rag, branch fix/security-hardening, head 147afff): the CI rerun of run 39410733286 passed both pytest-linux and postgres. The earlier postgres failure was the ingestion load-test throughput floor (46.2 < 50), which is hosted-runner noise.
- Did not merge #24, because the owner's "merge #23" covered only #23. Revert point if #24 merges: 01c53cc.
- Launched a read-only Opus security review in the background (`nexus-route launch --kind security --risk high --for security --since 3b98400`, task CR-SEC-1020). The brief covers figure_check, tools_result ordering and leakage, _live_tool_section authz and injection, claims_tools, and the 2026-10-19 fixes. Findings go into the next PR, not #24.

## Tried and ruled out
- Did not loosen the load-test floor, because one rerun was green. Revisit only if it fails again.

## Watch out
- The review output and brief are in session RAM scratch (/tmp/claude-1000/.../2372092f-.../scratchpad/runs/sec-1020). Read the result with jq on attempt-N-<model>/stdout.jsonl.
- Don't push to fix/security-hardening while #24 is open, or the new commits will land on that PR.
- The crag-pg-verify container (127.0.0.1:55432) is still running. Stop it at closeout.
