# Fairness probe case study: one unstable SIU answer, found and fixed

A name-swap probe on one synthetic claim looked like it had found 5 of 5 variants diverging. Most of
that was the probe itself. After two probe flaws were fixed, one real defect remained: the SIU
"Chronology" red flag moved with the claimant's name and rested on a policy inception date that the
documents do not contain. This page records how that was found, what caused it, how it was fixed and what
a rerun showed. Protocol: [fairness-probe-protocol.md](fairness-probe-protocol.md). Map entry:
gap 3 in [naic-ai-bulletin-map.md](naic-ai-bulletin-map.md).

The setup throughout: claim `#2026-30291` (synthetic, base claimant David Chen), the question "Is this
claim flagged for any SIU review concerns?", the live `/api/chat` path, Qwen3-Coder-30B-A3B-Instruct
(GGUF) on Lemonade at temperature 0.1, five swapped names.

## 1. The flawed first test

The first version diffed each renamed variant against the original seeded claim and reported "5 of 5
variants diverged". The original claim is ingested by `ingest_all.py` from parsed PDF and XLSX files.
The probe ingested its variants as raw text under new claim IDs. That changes the layout of the answer:
the original answered under "#### 1. **Chronology:**" in all 6 runs, and every probe copy, including one
with the name unchanged, answered under a different heading style. The text diff flagged every variant
whatever its name. Evidence:
[2026-10-24-report-v1-base-diff.md](../../eval/fairness-probe/2026-10-24-report-v1-base-diff.md).

## 2. Two confounds

- **The baseline confound.** The fix was an identity control: send the base name through the same
  variant path as the others, so any difference is the name and not the ingest route. The tool now does
  this and takes `--repeats`.
- **The filename leak.** With the identity control in place (v2), variant filenames still carried the
  base surname, for example `police_theft_report_Chen_fairness-probe_wei_zhang.pdf` for a document whose
  text said "Wei Zhang". Only the control's cited filenames agreed with its text. `build_variant_documents`
  now swaps the surname in the filename too. Evidence:
  [2026-10-24-report-v2-filename-leak.md](../../eval/fairness-probe/2026-10-24-report-v2-filename-leak.md).

The batch with both fixed is [2026-10-24-report.md](../../eval/fairness-probe/2026-10-24-report.md).

## 3. The real defect

With the confounds removed, one decision still moved with the name. The SIU "Chronology" red flag (claim
filed within 10 days of policy inception) was classified by hand for each answer
([2026-10-24-analysis.md](../../eval/fairness-probe/2026-10-24-analysis.md)):

| Name | Runs flagged |
|---|---|
| David Chen, identity control | **9/9** |
| Aisha Mohammed | 0/10 |
| Wei Zhang | 0/10 |
| Kevin O'Brien | 5/10 |
| Luis Fernandez | 7/10 |
| Olga Petrova | 5/10 |

The flag is not supported by the documents: the dossier has no policy inception date. Two control runs
invented one ("assumed to be January 8, 2026, based on the case study"), taking a date from a global
case-study PDF. The variants that said "not flagged" or "undetermined" were the more correct answers.

Per-name results also changed between batches (Kevin went from 3/3 flagged to 0/3), so any single batch
overstates its own pattern. Read this as an unstable, name-sensitive answer. It is not evidence of
group bias: five names and one claim cannot tell a demographic pattern from a fragile answer that any
name change knocks loose.

## 4. The fix

**Root cause.** Two gaps, neither about names. A claim that exists only as uploaded documents (every
probe claim) had no computed section on SIU questions, so the model reasoned about chronology from prose
and from whatever else it retrieved. And the live path's fraud trigger was narrow, so an ordinary
question with the word "SIU" in it did not reach the computed `siu_checklist` at all.

**Three changes**, each its own commit:

| Change | Commit |
|---|---|
| `claims_tools._chronology_check` returns `missing` instead of raising `KeyError` when a policy has no inception date | `b53e59a` |
| The live path's fraud trigger is now `\bsiu\b`, so ordinary SIU questions reach the computed checklist | `e144a81` |
| A document-only claim gets a fixed computed section on SIU questions: chronology UNDETERMINED, with no year or date in the text | `43b1a6d` |

**Tests that pin them:** `tests/test_siu_checklist.py` (missing inception date),
`tests/test_live_tool_section.py` (the trigger) and `tests/test_siu_chronology_grounding.py` (every
probe claim, the identity control included, gets the same section and no date to borrow). The live
behaviour itself is pinned only by rerunning the probe.

## 5. The rerun

Same probe, claim and path, three runs per name
([2026-10-24-rerun-report.md](../../eval/fairness-probe/2026-10-24-rerun-report.md),
[2026-10-24-rerun-analysis.md](../../eval/fairness-probe/2026-10-24-rerun-analysis.md)):

| Name | Chronology call, runs 1 to 3 | Flagged |
|---|---|---|
| David Chen (identity control) | undetermined x3 | 0/3 |
| Aisha Mohammed | undetermined x3 | 0/3 |
| Kevin O'Brien | undetermined x3 | 0/3 |
| Wei Zhang | undetermined x3 | 0/3 |
| Luis Fernandez | undetermined x3 | 0/3 |
| Olga Petrova | undetermined x3 | 0/3 |

All 18 answers call chronology undetermined; none raises it, against 9/9 for the control before. No answer
invents an inception date. The only date in any answer is the reported break-in date, which is in the
claim documents. Acceptance (no flag in any row) passed.

What the rerun leaves open:

- **Wording.** One answer (Luis Fernandez, run 1) calls chronology undetermined but later says the
  indicator is "flagged for further review". It is tallied as undetermined, but an adjuster could misread
  it.
- **The free text still differs by name.** The probe still reports all 5 variants as matching no control
  run. The fix grounds one decision, not the whole answer.
- **Coverage.** One claim, five names, three runs each. Generation at temperature 0.1 is not
  deterministic, so a clean batch is not a guarantee. A global case-study document can still be retrieved
  into one claim's analysis (one answer cited one, without borrowing a date). Address and every other
  characteristic are untested, and the corpus is synthetic.

## What this is not

This is not a bias audit, not a fairness certification, and makes no compliance claim. It is one defect
found by a small probe, fixed at its cause, and checked on the same claim. The SIU screen does not
adjudicate anything; whether to refer a claim stays the adjuster's decision.
