# Demo shot list: 90-second screen recording

## Before recording

1. Seed and ingest the synthetic corpus:

   ```bash
   .venv/bin/python generate_auto_pdfs.py
   .venv/bin/python create_sample_files.py
   .venv/bin/python ingest_all.py
   ```

2. Start the server on a free port (8010 here; use any free port):

   ```bash
   .venv/bin/python -m uvicorn backend.app:app --port 8010
   ```

3. Open `http://localhost:8010` in a browser window at 1280×720 or larger. The default `development` profile signs in with the labelled development identity, so there is no sign-in screen locally. With no local model loaded, answers come from simulation mode. They use the same tools, numbers and citations as the write-ups.

All claims, SOPs and documents are synthetic.

## Shots

| # | Time | On screen | Action | Caption |
|---|---|---|---|---|
| 1 | 0–8 s | Claims workspace, claims list on the left | Hold on the list. | Synthetic auto claims. Every answer cites its source. |
| 2 | 8–28 s | #2026-99382 (Matthew Sterling) | Click the claim, then **Review Supplement**. Scroll the S1 line table to the reinspection line. | Supplement S1: billed $1,205.00, supported $1,006.00. Reinspection required: supplements are 56.5% of the original estimate, over 25%. |
| 3 | 28–46 s | #2026-71845 (Marcus Webb) | Click the claim, then **Triage Subrogation**. | Third party 70% at fault: refer to subrogation. Expected recovery $605.50; deductible reimbursement $605.50. |
| 4 | 46–60 s | #2026-84410 (Tyler Brooks) | Click the claim, then **SIU Indicator Checklist**. | Three red flags present, each with its SOP rule and evidence. Referral is the adjuster's decision. |
| 5 | 60–75 s | #2026-55912 (Elena Rostova) | Click the claim, then **SIU Indicator Checklist**. Hold on the coverage notes. | The record says "SIU Flagged". No SOP indicator supports it, and the answer says so instead of inventing a reason. |
| 6 | 75–90 s | README, "Results" section | Scroll to the golden-set table. | 34 claims questions, scored by a local judge: context recall 0.983, reference-fact coverage 0.840. |

The shots add up to 90 seconds. If a render runs long, trim shot 1 first.

## Notes

- Use only the four mode buttons: **Review Supplement**, **Explain Total Loss**, **Triage Subrogation**, **SIU Indicator Checklist**. To show the total-loss mode instead of shot 3, click **Explain Total Loss** and caption the threshold result on screen. The example in [total-loss-explainer.md](total-loss-explainer.md) uses a typed prompt with its own ACV and salvage figures, not the button, so its numbers won't match the button's output.
- Shot 5 is the refusal shot. The live-model path also refuses outright when retrieval finds no supporting document. A short demo can't trigger that reliably on the seeded corpus, so the recording shows the SIU probe instead.
- Captions quote [supplement-review.md](supplement-review.md), [subrogation-triage.md](subrogation-triage.md), [siu-checklist.md](siu-checklist.md) and the README Results table. If a number on screen differs, re-seed (step 1) before recording, and don't edit the caption to match.
