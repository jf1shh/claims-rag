---
title: "ClaimsRAG adjuster-scenario hardening: deps, subrogation, coverage, SIU"
author: Jared
date: 2026-10-20
branch: fix/security-hardening
cost: { minutes: 3, tokens: 105583 }
touches:
  - backend/claims_tools.py
---

## Decided
- Branch fix/security-hardening: pypdf 6.16.1->6.21.1, PyJWT 2.13.0->2.15.1 (commit de7cee7). dependency-audit 26 -> 4; the 4 left are the reviewed-unreachable ragas/diskcache notes.
- Work order: subrogation triage (claims_tools.subrogation_triage) -> coverage_check + two scenario claims (#2026-71845 Webb TX comparative fault/commercial truck, #2026-84410 Brooks FL paper accident) -> siu_checklist replacing canned fraud text -> claims tools on the live LLM path (code appends the computed section after guard/provenance; add a figure check).
- Briefs live in the session scratchpad (subro.md, cov.md, siu.md); each worker gets one, run in order because they edit the same files.

## Tried and ruled out
- Leaving the canned fraud narratives in place: they end in decisions (Deny/Approve), cite telematics_log_Sterling.pdf (doesn't exist), and contradict the police report (Jan 10/14 mph vs Jan 12/~18 mph).

## Watch out
- Chen key-scratch exclusion cites the fraud SOP for "rust", but no dossier doc records rust; add inspection_photos_Chen.pdf as evidence.
- The audit-type router matches substrings: "separate" counts as a "rate" query and "capacity" as a "cap" query. Use whole-word regexes.
- provenance_check redacts filenames that weren't retrieved, so a tool citation table appended before the check would get redacted.
- Coverage and SIU workers touch the same files as subrogation: one writer at a time.
