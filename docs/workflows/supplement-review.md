# Supplement Review

Checks every line a shop adds after teardown against the labor caps, the vehicle's parts standard and the betterment rules, then says whether the supplement needs photos, a field reinspection, or a response by a deadline.

## The adjuster's problem

Supplements arrive after the first estimate is approved, usually after teardown finds hidden damage. Each added line has to be checked against the same labor caps as the original, the parts standard for that vehicle, and the betterment rules for wear items. The adjuster also has to know whether the supplement crosses the reinspection threshold (one supplement over $1,500, or all supplements over 25% of the original approved estimate) and when the response is due. Doing that by hand for every line is slow and easy to get wrong.

## What it does

- Re-prices each added line: labor over the SOP cap for its labor type, betterment on wear items (tires, batteries), and the supported amount after both.
- Flags a part type that doesn't fit the vehicle's parts standard (for example OEM billed on a non-safety part when the standard calls for LKQ or aftermarket).
- Applies the reinspection threshold to this supplement and to all supplements to date.
- Holds the supplement when the SOP's required documentation (itemized lines, teardown photos) is missing, and starts the response clock (2 business days) only once it is complete.
- Ends with a tool-inputs table that cites the SOP, rider or guideline section behind every number. The code computes; a language model, when one is loaded, only explains.

## Example

Claim #2026-99382 (Matthew Sterling), **Review Supplement** button:

> **Recommended next step: Reinspection recommended before the adjuster's decision**
>
> Supplement S1 (received 2026-01-14): billed $1,205.00, supported $1,006.00
>
> | # | Added line | Billed | Labor over cap | Betterment | Supported |
> | --- | --- | --- | --- | --- | --- |
> | 1 | Replace Rear Bumper Reinforcement Bar | $625.00 | $39.00 | $0.00 | $586.00 |
> | 2 | Repair Hidden Rear Rail Kink | $340.00 | $40.00 | $0.00 | $300.00 |
> | 3 | Replace 12V Battery (cracked case) | $240.00 | $0.00 | $120.00 | $120.00 |
>
> Reinspection: required: supplements to date ($1,205.00) are 56.5% of the original approved estimate, over 25%. Response due: 2026-01-16 (2 business days).

Line 1 is billed at $75.00/hr against the $62.00/hr Sheet Metal Repair cap; the battery betterment is $240.00 x 30/60 months, cited to `Guideline_Betterment_Adjustments.docx`.

On #2026-10492 (Sarah Jenkins) the same button returns **HOLD: documentation incomplete**: teardown photos are missing, an OEM door shell is billed on a non-safety part, the two front tires carry $240.00 of betterment, and the $2,105.00 supplement is over the $1,500.00 single-supplement threshold.

## How it is tested

- **Unit tests:** [`test_supplement_review.py`](../../tests/test_supplement_review.py), 20 test functions, against the synthetic SOP tables and both demo supplements.
- **Golden eval cases**, scored 2026-10-21 (context precision / recall / faithfulness / reference-fact coverage):
  - `supplement-reinspection-threshold`: 1.00 / 1.00 / 0.88 / 0.75. When does a supplement need a field reinspection?
  - `betterment-tires`: 1.00 / 1.00 / 1.00 / 1.00. How is tire betterment calculated, and which parts never take betterment?
- **Eval gap:** there is no claim-scoped golden question that runs the button end to end yet. The golden cases test the underlying rules; the button output is covered by the unit tests.

## Limits

- The SOPs, caps, thresholds and claims are synthetic.
- The example shows the simulation path (no model loaded), where every number comes from [`backend/claims_tools.py`](../../backend/claims_tools.py). With a local model loaded, the model writes the explanation around the same tool output.
- Advisory only: the output is not a coverage or claim decision. The adjuster decides whether to approve, hold or reinspect.

## Where it fits

Estimating and repair-review platforms such as CCC, Mitchell, Solera or Snapsheet, where supplements arrive from shops and wait for an adjuster's review. This mode shows how a line-by-line, cited check could sit at that step.
