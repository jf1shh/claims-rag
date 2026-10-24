# Fairness probe protocol

Closes one part of governance gap 3, "Bias and unfair-discrimination analysis"
([naic-ai-bulletin-map.md](naic-ai-bulletin-map.md)): check that output doesn't vary with a
claimant's name when the claim facts are held constant.

## Where a name could, and couldn't, influence an answer

`backend/claims_tools.py`'s deterministic rule functions (`labor_rate_check`, `siu_checklist`,
`oem_rider_check`, `subrogation_triage`, `total_loss_check`, and the rest) take no claimant name or
address parameter at all. They are not a bias surface, because there is nothing in their inputs for a
name to vary.

The one place a claimant's name can reach an answer is the free-text agentic path: it retrieves and
reads the claim's own narrative documents (police reports, inspection logs, receipts, spreadsheets),
and the claimant's name is written directly into the text of some of those documents (`ingest_all.py`'s
`SEED_CLAIM_DOCUMENTS`). If the model's answer changes when that name changes and nothing else does,
that is a finding.

## Method

`eval/build_fairness_probe.py`:

1. Picks one seeded claim's documents (default `#2026-30291`, David Chen's theft claim).
2. Finds the claimant's name in that claim's document text (`claim_name` — see "Which claims this
   works on" below).
3. For each name in a fixed roster (`NAME_ROSTER`: five names spanning different naming traditions,
   not labeled with an ethnicity in code or docs — the point is that the model should ignore this
   input, not that this tool sorts names into demographic buckets), builds a full copy of that claim's
   documents with the claimant's name replaced and nothing else, and ingests the copy under a
   throwaway `claim_id` (`<original>-fairness-probe-<name>`) and throwaway filenames
   (`<original>_fairness-probe_<name>.<ext>`) that can never collide with real data.
4. Asks the *same* claim-scoped question against the base claim and every variant, via the live
   `/api/chat` path (`eval/run_eval.py`'s `chat()`, same call used by
   [the expert-review packet](expert-review-protocol.md)).
5. Strips every candidate name out of every answer and diffs what's left. A variant whose
   name-stripped answer differs from the base's is a finding.
6. Deletes every variant document it ingested, in a `finally` block, so the run leaves the store as it
   found it even if a `/api/chat` call fails partway through.

## Which claims this works on

The claimant's name doesn't appear in every seeded claim's document text — `#2026-55912` (Elena
Rostova)'s telematics and diagnostic reports never name her in the text, only in the filename. For a
claim like that, `claim_name()` correctly returns `None` rather than guessing, and the probe needs
`--base-name` supplied explicitly. The default claim (`#2026-30291`) was chosen because the name
appears in multiple documents' text, which is also the realistic case: a claim where no document ever
names the claimant is not a case this probe is needed for.

## Running a probe

Requires the backend server running and a model loaded in Lemonade (same prerequisites as
`eval/run_eval.py` and the expert-review packet):

```bash
.venv/bin/uvicorn backend.app:app --port 8000      # separate shell
.venv/bin/python eval/build_fairness_probe.py --out eval/fairness_probe_report.md
```

## Reading the result

No divergence after name-stripping across all five variants is evidence of absence, not proof — five
names and one claim is a narrow probe, run against a synthetic corpus the model may answer
differently from a real one. A divergence is worth investigating even if it looks minor: check whether
the difference tracks the name specifically (rerun with a sixth name to rule out model
non-determinism) before treating it as a real finding.

## Recording a completed run

A completed probe is evidence, not scratch output — commit the report under `eval/fairness-probe/`
with a dated filename, e.g. `eval/fairness-probe/2026-MM-DD-report.md`, and summarize the result in
`docs/current-state.md`. Update `docs/governance/naic-ai-bulletin-map.md` gap 3's status only once a
committed report exists — this document describes the protocol, not a claim that a probe has already
run.

## What this does not close

This probes name as a bias channel through the free-text answer path, on a synthetic corpus, with a
fixed five-name roster. It does not probe address (the seeded documents don't carry a claimant address
separate from the loss location, and changing the loss location would change the facts under test,
defeating the point of the probe), does not probe any other protected characteristic, and does not
constitute a bias audit against real claim data or real claimants.
