# Expert review protocol

Closes one half of governance gap 2, "Validation on real claims with expert review"
([naic-ai-bulletin-map.md](naic-ai-bulletin-map.md)). That gap has two independent parts:

| Part | Status | Why |
|---|---|---|
| Real (not synthetic) claim files | **Gap, out of reach here** | Needs an insurer pilot under that insurer's own data controls (consent, retention, access scoping). There is no insurer and no real claim data in this repo, and there should not be — see Critical Constraints in the top-level `CLAUDE.md` and `docs/release-checklist.md`. |
| Expert review of answers | **Built** (this doc + `eval/build_expert_review_packet.py`) | Does not need real claims. A human claims expert reviewing the system's actual live answers is a different check than the automated judge, and is achievable now. |

A real pilot still needs both parts together — expert review of synthetic answers does not prove
accuracy on real claims. What this closes is the part that was entirely missing: nothing before this
had a human claims professional, rather than an LLM judge, score the system's output.

## Why a second reviewer, beyond the judge

`eval/run_eval.py`'s Faithfulness and FactualCorrectness scores
([README Results](../../README.md#results)) measure whether an answer is grounded in its retrieved
context and covers the reference answer's facts. Both are judged by the same class of model the
system uses to generate — correlated blind spots, not independent verification. A claims handler asks
a different question: not "does this match the source text" but "would I act on this, as written, on
a real file." Those can diverge: an answer can be perfectly faithful to a synthetic SOP sentence and
still be the kind of statement a handler would never send to a claimant or write into a file note
unqualified.

## Sampling

`eval.build_expert_review_packet.sample_queries` takes the 34-question golden set
([golden_queries.py](../../eval/golden_queries.py)) and picks:

- One query per distinct `claim_id` (currently 6 claim dossiers) — every claim gets reviewed.
- A deterministic random slice of the `claim_id=None` (global policy/guideline) queries, default 8
  (`--n-global`), seeded (default 42) so the same packet reproduces across runs unless the seed changes.

~14 questions by default: enough coverage without being a full-day task for the reviewer.

## Generating a packet

Requires the backend server running and a model loaded in Lemonade (same prerequisites as
`eval/run_eval.py`):

```bash
.venv/bin/uvicorn backend.app:app --port 8000      # separate shell
.venv/bin/python eval/build_expert_review_packet.py --out eval/expert_review_packet.md
```

Each question's live `/api/chat` answer is included with its cited sources, plus the matching
automated faithfulness/correctness scores from `eval/results.json` (or `--results path/to/results.json`)
*for comparison only* — the expert is not asked to agree with the judge, only to apply independent
domain judgment.

## Rubric

For each question, the reviewer answers, in writing, directly in the packet:

1. **Would a claims handler act on this answer as written?** (yes / with caveats / no)
2. **Does it match real claims-handling practice, beyond mere faithfulness to the source text?** (1–5)
3. **What would a handler catch that the automated judge wouldn't?** (free text — this is the part the
   judge cannot produce: domain-specific red flags, missing qualifiers, a technically-true answer that
   would mislead in practice.)

A "no" or a 1–2 score on any question is a finding, not a failure of the packet — record it (the next
run of this protocol, or a code fix, should reference the specific question id).

## Recording a completed review

A completed review is evidence, not scratch output — commit the filled-in packet under
`eval/expert-review/` with a dated filename, e.g. `eval/expert-review/2026-MM-DD-packet.md`, and
summarize findings in `docs/current-state.md` under the session that did it. Update
`docs/governance/naic-ai-bulletin-map.md` gap 2's status only once a completed, committed review
exists — this document describes the protocol, not a claim that a review has already happened.

## What this does not close

Running this protocol validates expert-reviewability of synthetic-corpus answers. It does not, and
cannot, validate accuracy on real claims — that half of gap 2 stays a Gap pending an insurer pilot.
