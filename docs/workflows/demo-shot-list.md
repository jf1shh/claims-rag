# Demo Shot List: 90-Second Screen Record

## Before recording

- Seed and ingest: `.venv/bin/python ingest_all.py` (populates synthetic documents and claims).
- Start the server on any free port: `.venv/bin/python -m uvicorn backend.app:app --port 8010` (use a different port if 8000 is in use on the host machine).
- Open the browser at **1280×720 or larger** resolution. Navigate to `http://localhost:8010` (or the port you chose).
- All data shown is synthetic (SOP tables, claims, and documents); no real policyholder or claim data is used.

---

## Shot List

| # | Time (cumulative) | On screen | Action | Caption |
|---|---|---|---|---|
| 1 | 0–10 s | ClaimsRAG home | Sign in (any email). | Sign in to ClaimsRAG. |
| 2 | 10–20 s | Claim list | Click on claim #2026-99382 (Matthew Sterling, supplement review demo case). | Load claim #2026-99382: supplement case. |
| 3 | 20–30 s | Claim detail, buttons visible | Click **Review Supplement**. | Review Supplement: assess reinspection threshold, labor caps, betterment. |
| 4 | 30–50 s | Supplement line table and reinspection result | Show the line-item table (billed vs. supported, labor over cap, betterment flags) and the reinspection recommendation. Pan down to see the full "Tool inputs and citations" table. | Supplement S1: $1,205.00 billed, $1,006.00 supported. Reinspection required (56.5% of original estimate). |
| 5 | 50–60 s | A claim with no relevant source, or the answer guard holding a forged claim decision | Ask the system a question with no supporting document (e.g., "What is the policy on rental car coverage?" if no rental guidelines are in the claim folder). Alternatively, if the answer guard is visible, show a blocked or flagged response. | Refusal: the system does not invent answers when sources are missing. |
| 6 | 60–75 s | README Results section (eval table) | Scroll to the README and show the Results section with the golden-eval coverage scores. | Evaluated on 34 questions: context precision 0.825, recall 0.983, faithfulness 0.839, reference-fact coverage 0.840. |
| 7 | 75–90 s | Home button, close | Click home or close the browser. | Thank you. End screen. |

---

## Notes

- **Do not invent UI elements** beyond the buttons listed: "Review Supplement", "Explain Total Loss", "Triage Subrogation", "SIU Indicator Checklist" (and the other buttons on the home page, which are not part of P5).
- **Timing is approximate.** If a section takes longer to render, adjust. The total should stay around 90 seconds.
- **Synthetic data only.** All claims, SOP tables, documents, and tool outputs are generated for demonstration and do not represent actual insurance data.
- **Port and resolution.** Use any free port (e.g., 8010 if 8000 is busy); browser resolution 1280×720 or larger to ensure readability.
