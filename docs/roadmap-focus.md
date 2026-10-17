# ClaimsRAG roadmap: claims workflows that hiring teams recognise

> Set 2026-10-16. ClaimsRAG is the lead portfolio project for a job search aimed at AI in
> insurance claims: InsurTech and claims-software companies, carrier AI teams, and
> insurance-domain AI evaluation. This roadmap ranks work by what a claims-AI hiring team would
> notice. It sits on top of `docs/current-state.md`, which still records the platform phases.

## Where it stands (from the repo, 2026-10-16)

- Platform depth is already high: hybrid retrieval with reranking, an agentic router, an answer
  guard, a conflict check, auth/RBAC, audit, rate limits, streamed chat, a Postgres/pgvector leg,
  and load tests. 660 tests run in about 13 s.
- Golden eval (`eval/results.json`): 19 queries; hybrid context recall 0.965, faithfulness 0.892,
  correctness 0.797, all judged by one local model.
- Adversarial eval (27 cases, 2026-09-26, guarded run): overall pass 0.615, conflict 0.9, and a
  prompt-injection **attack-success rate of 0.562**.
- **Domain coverage gap** (`crag coverage`, 2026-10-16): the 19 golden queries cover OEM parts,
  deductibles, rental, fraud/SIU, labor rates and total loss. They have no subrogation, supplement
  review, salvage, diminished value, betterment, liability or storage/towing cases. Those are the
  workflows the target companies sell. The adversarial suite already mentions most of them.
- **The demo's "Calculator Tool" steps are canned** in the labelled simulation mode
  (`backend/agentic_router.py` `_run_simulated_agent`, hard-coded per demo claim). This is
  disclosed in the README, but an engineer reading the code will see strings, not computation.

## Priorities

### P1. Real claims tools in place of canned simulation

**Done 2026-10-16** (`backend/claims_tools.py`, 28 unit tests + 10 router tests). Labor, OEM,
total-loss and settlement answers are computed for all four demo claims and end with a
tool-inputs table citing document and section. Fraud/SIU narratives are still canned (not P1).
Computing exposed what the strings hid: the canned answers cited a nonexistent
`Regional_Labor_Rates_2026.xlsx` and called estimates compliant that exceed the SOP caps.

Deterministic, tested functions that the router calls and that compute from the ingested
synthetic documents, with every input cited:

| Tool | What it computes | Who cares |
|---|---|---|
| `labor_rate_check` | Estimate labor lines against the regional cap table | CCC, Mitchell, Solera, Snapsheet |
| `oem_rider_check` | OEM-parts rider eligibility (vehicle age/mileage window, endorsement on policy) | carriers, CCC |
| `total_loss_check` | Total-loss threshold or total-loss formula by state (synthetic table), ACV vs repair + salvage | CCC, Mitchell, carriers |
| `deductible_apply` | Deductible and endorsement math | carriers |

Acceptance: the simulation's hard-coded numbers come from these tools. Each tool has unit tests
against the synthetic tables, and the chat shows the tool inputs with citations. The LLM explains;
the code computes.

### P2. Close the domain eval gaps

**Done 2026-10-16** (branch `feat/p2-domain-eval`): 28 golden queries; `crag coverage` flags only PII/privacy. Two same-day runs: hybrid P/R 0.865/0.991 in both, correctness 0.862/0.863, faithfulness 0.798/0.825. The new nine are easier (one short source each): correctness 0.956 vs 0.818 on the original nineteen.

Add golden queries (with synthetic source docs where none exist) for subrogation, supplement
review, salvage, diminished value, betterment, liability and storage/towing, so `crag coverage`
shows no `GAP` except PII/privacy, which the adversarial and guard suites cover. Re-run the golden eval and record dated results. Run it twice to separate judge variance
from drift, which is the open item in `current-state.md`.

### P3. Prompt-injection hardening

**Done 2026-10-16** (PR #16): after the rerun the baseline was 0.25 known / 0.58 held-out, and the provenance check brought it to 0.06 / 0.25. Conflicts stayed at 11/11 and honest controls at 4/4. See `docs/adversarial-evaluation.md` fix 4.

Bring the injection attack-success rate (0.562) down on the existing adversarial suite without
lowering the conflict pass rate (0.9). Record before/after with dates. Insurers' security reviews ask about this first.

### P4. Workflow modes that match the target companies

One "audit" mode each, built on P1 tools and P2 evals, with cited per-line output and advisory-only
wording:

1. **Supplement review**: original estimate vs supplement, each added line checked against
   guidelines (labor caps, OEM rider, betterment). Hook for CCC, Mitchell, Solera, Snapsheet.
2. **Total-loss explainer**: shows the threshold math and the state rule it came from.
3. **Subrogation triage**: from the police report and statements, flags subrogation potential and
   the evidence for it. Hook for carriers and Five Sigma.
4. **SIU indicator checklist**: cited fraud indicators, never a decision. Hook for Shift Technology.

### P5. Show it

- 90-second demo video: one supplement review end to end, a refusal, and the eval table.
- README: a "Claims workflows" table near the top linking each mode to its eval cases.
- One short write-up per mode, used as the outreach hook for that segment.

### P6. Governance evidence (lane B carriers)

Map the project to the NAIC AI Model Bulletin themes (governance, risk management, testing, data
and third-party controls) using what already exists: audit log, RBAC, answer guard, adversarial
suite, advisory-only stance. This matches Phase 6.3 "compliance evidence" in `current-state.md`.

### Paused (low hiring signal for this search)

Phase 6.1 Postgres/HTTP load A/B, reranker batching tuning, OIDC against a real IdP, KMS, and
cutover (6.4). These stay recorded in `current-state.md` and resume after an offer or if a target
company asks.

## How to work on it cheaply

| Need | Command | Instead of |
|---|---|---|
| Tests and gate verdict | `.venv/bin/python scripts/crag.py check` | raw pytest output and 50+ gate warnings |
| Saved eval numbers | `scripts/crag.py evals` | reading the eval JSON files |
| Domain coverage gaps | `scripts/crag.py coverage` | reading `eval/golden_queries.py` |
| Branch, dirty files, services up | `scripts/crag.py status` | several git and curl calls |

Build order: P1 → P2 → P3 → P4 (supplement review first) → P5 → P6. Each P is its own branch and
PR. Each starts with `crag check` and `crag coverage` and ends with them.
