# Proof roadmap: from portfolio project to evidence an insurer can act on

Status: **spec, approved in design 2026-10-24**. Each workstream gets its own implementation plan when
it starts, executed one at a time.

## Why this exists

ClaimsRAG already works: hybrid retrieval, an agentic router, deterministic claims tools, adversarial
and drift evals, and a governance map against the NAIC AI bulletin. What it doesn't have yet is
evidence for the three people who decide whether a claims AI tool is worth adopting:

| Evaluator | Question they ask | Workstream that answers it |
|---|---|---|
| Claims executive | Does it save handler time without hurting accuracy? | b. Timed study |
| Compliance / model risk | Has a qualified human checked it, and is it fair? | a. Expert validation, c. Governance story |
| IT / security | Will it fit our stack and controls? | d. Enterprise fit |
| All three | Can I see it doing a handler's job? | e. Demo |

All data in this repo is synthetic: every SOP, claim, claimant and figure was generated for the project
(the README says so in its opening line; week 1 adds an explicit data-provenance note to its About
section: no employer material, built on the owner's own time and equipment). Every result below is
labelled as measured on synthetic data.

## Sequencing

Evidence first. The owner's time is the scarce input (about 5 hours a week); Claude builds tooling in
parallel.

| Week | Owner | Build |
|---|---|---|
| 1 | Score the 2026-10-24 packet; recruit reviewers | c. grounding fix + probe rerun; a. agreement script; b. study tool; README provenance note |
| 2 | b. Study sitting 1 | d. Enterprise reference profile |
| 3 | b. Study sitting 2; reviewer scores return | d. ClaimCenter-shaped mock adapter; b. business case |
| 4 | e. Demo recording (optional voiceover) | e. Recorder extension; README "For a hiring manager" update |

## a. Expert validation

Turns the 2026-10-24 packet (`eval/expert-review/2026-10-24-packet.md`) into a scored, multi-rater
review.

- The owner (17 years in auto claims) scores the packet using the rubric in
  [docs/governance/expert-review-protocol.md](../governance/expert-review-protocol.md).
- 2–3 more adjusters or appraisers each score an anonymized copy on their own, without seeing each
  other's scores or the LLM judge's scores.
- New `eval/expert-review/agreement.py` (stdlib only, so it runs in the PR suite): weighted Cohen's
  kappa for two raters and Fleiss' kappa for three or more, plus % exact and % within one point, with
  a per-question list of disagreements.

Acceptance:
- At least 3 raters, the owner included. Scored packets are committed under `eval/expert-review/`.
- The agreement report states n, the corpus (synthetic) and its limits.
- Gap 2 in [docs/governance/naic-ai-bulletin-map.md](../governance/naic-ai-bulletin-map.md) is updated
  to "expert-reviewed on synthetic data". The real-claims half stays a Gap.
- Reviewers are named only with consent. No reviewer brings in employer data.

## b. Timed study

Measures handler time with and without ClaimsRAG.

- 20 questions from the synthetic corpus (claim dossiers plus global SOP questions), split into two
  sets matched for difficulty (question type, number of documents needed).
- Crossover: sitting 1 is set A by manual lookup (PDFs, Ctrl-F allowed) and set B with ClaimsRAG;
  sitting 2 swaps them, with a different but matched question pool, so question difficulty and the
  learning effect don't masquerade as tool effect.
- New `eval/timed_study/` tool: shows one question at a time, records start, stop and the answer, and
  writes a CSV. Answers are scored against the reference answers with the same rubric as (a).

Acceptance:
- Median minutes per question and accuracy for each method, with the spread.
- Limits stated plainly: one participant, who also built the tool; synthetic corpus; small n.
- A one-page business case (`docs/go-to-market/business-case.md`): minutes saved × lookups per
  adjuster per day × loaded hourly cost, with every assumption shown and sourced (for example, BLS
  wage data for claims adjusters). No number appears without its source or the word "assumption".

## c. Governance story

Finishes what the 2026-10-24 fairness probe found
([analysis](../../eval/fairness-probe/2026-10-24-analysis.md)): the SIU chronology flag changed with
the claimant's name and wasn't supported by the file.

- Grounding fix: when the file has no policy inception date, the free-text SIU answer must say the
  chronology check is undetermined. Do it either by routing the check through
  `backend/claims_tools.py` `siu_checklist`, which already implements the rule from structured
  dates, or by requiring the date before the flag can be raised. TDD, plus an adversarial or golden
  case that pins it.
- Rerun `eval/build_fairness_probe.py --repeats 3`.
- Write `docs/governance/fairness-case-study.md`: flawed test → two confounds → real defect → fix →
  rerun.

Acceptance:
- In the rerun, no chronology decision depends on the name (5 names × 3 runs, plus the identity
  control).
- The case study cites only committed evidence files and does not claim a bias audit.

## d. Enterprise fit

Shows how an insurer's IT team would run it, without claiming any certification or partnership.

- `docs/operations/enterprise-reference-profile.md`: Azure OpenAI and AWS Bedrock behind the
  provider-neutral `backend/llm_client.py`, written from the vendors' live documentation, never from
  memory. The doc also points to what already exists: Postgres + pgvector multi-tenancy
  ([docs/enterprise-migration.md](../enterprise-migration.md)), authentication and RBAC
  (`backend/authn.py`), rate limits and the audit log.
- A mock intake adapter shaped like Guidewire ClaimCenter's public Cloud API (claim, exposures,
  documents), written from Guidewire's published API docs (see also
  [docs/research/2026-08-28-guidewire-palisades-integration-notes.md](../research/2026-08-28-guidewire-palisades-integration-notes.md)).
  It maps a claim payload into a ClaimsRAG claim folder.

Acceptance:
- The adapter has offline contract tests on fixture JSON (no network, empty model cache, per the
  repo's API contract test rule).
- Both documents say "reference" and "mock" and name what was not tested against a real tenant.

## e. Demo

A 3-minute adjuster-workflow video, not an architecture tour.

- Script: a new theft claim arrives → the SIU question → supplement review → a drafted reply to the
  insured, with citations at each step.
- Extends the 2026-10-21 Playwright recorder ([docs/build-history.md](../build-history.md), "Demo video
  recorded").

Acceptance:
- Runs 3:00 or less. Every caption is checked against the app's actual answer text before export and
  never edited to fit.
- Linked from the README's existing "For a hiring manager" section, which is updated to link (a)–(d).

## Out of scope

- Any real claim data or employer material. A real-data pilot needs an insurer's data controls and
  stays a documented Gap.
- Claims of compliance, certification or vendor partnership.
