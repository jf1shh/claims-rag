---
paths:
  - "frontend/**"
---

# Frontend critical constraints

* **No String-Interpolated Inline Event Handlers in the Frontend**: never build `onclick="func('${value}')"` (or any inline handler) by interpolating a value into an HTML/JS string. `escapeHtml()` does not survive the HTML-attribute-then-inline-JS double-parse (entity-escaped quotes decode back to real quotes before the JS engine sees them). Use `addEventListener` with real JS values, or `data-*` attributes read via `.dataset`.
* **Dynamic Content Rendered into the Claims Queue/Estimate Table Must Use `textContent`, Not Interpolated `innerHTML`**: `CLAIMS_DATA` (served by `GET /api/claims`) is currently a hardcoded backend constant, so this isn't exploitable today, but `setupClaimsCases()`/`loadCaseFolder()` in `frontend/app.js` were building list/table markup via `innerHTML` template literals with raw `${c.id}`/`${c.status}`/`${row.op}`-style interpolation -- the same stored-XSS shape already fixed for document filenames in Phase 10, just not applied to this later (Phase 13) code path. Any future feature that makes a claim field user-editable would reopen it. Fixed by building nodes and setting `.textContent`/`.classList`, matching `renderDocuments`/`renderSources`.
