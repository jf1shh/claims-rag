---
title: "Governance evidence map: pin evidence by text, label insurer duties, list endpoints are not audited"
author: Jared
date: 2026-10-21
branch: feat/p6-governance-evidence
cost: { minutes: 3, tokens: 78634 }
touches:
  - docs/build-history.md
  - docs/roadmap-focus.md
  - docs/governance/naic-ai-bulletin-map.md
  - tests/test_governance_map.py
---

## Decided
- docs/governance/naic-ai-bulletin-map.md maps each NAIC AI Model Bulletin item (2023-12-04; Section 3 items 1.0-4.0 and the Section 4 exam list) to Built, Partial, Insurer or Gap. It never says "compliant": the bulletin binds insurers, so organisational duties (board, committees, vendor contracts, consumer notice, training) are labelled Insurer.
- tests/test_governance_map.py pins each claim to a `path` + exact text string that must still exist in the cited file, not a line number. Unrelated edits don't break it; renaming a control or changing a default (e.g. ANSWER_GUARD_MODE) does, and that is intended.
- The demo video (98 s, Simulation Mode) lives in the owner's notes hub, not the repo. Its captions were checked against the dumped answer text before export.

## Tried and ruled out
- Claiming "every authenticated action is audited": the list endpoints (/api/documents, /api/claims, /api/jobs) are not audited. The map says so.

## Watch out
- If you edit a file the map cites, run `pytest tests/test_governance_map.py`. A failure means the map's claim needs re-checking, not that the pinned string needs blindly updating.
- trail check flags about 20 false positives on the map's prose. Read each one; don't treat them as conflicts.
- Playwright records a page until its context closes. Trim the first page of a multi-page recording where its last caption clears.
