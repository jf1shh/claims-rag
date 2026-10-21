# Total Loss Explainer

Shows why a vehicle is or isn't a total loss: the state rule it came from, the threshold math step by step, the valuation sources, and what has to happen next.

## The adjuster's problem

A total-loss call depends on the loss state's rule, a repair cost that only counts supported lines, and an ACV backed by acceptable valuation sources. The adjuster then has to explain the result to the policyholder and handle salvage, title, storage and transfer deadlines. When the reasoning lives only in the adjuster's head, it is hard to review and hard to defend.

## What it does

- Takes the loss state from the repair facility and applies that state's threshold rule (synthetic table).
- Builds the repair cost from the original estimate at the SOP labor caps plus any supported supplements, and says how far it is from the threshold and whether supplements flip the result.
- Checks the valuation file: at least two independent sources, with Kelley Blue Book treated as secondary.
- Shows the owner-retains-salvage figure and lists the follow-on steps: title branding, the storage cap and cut-off, and the salvage transfer deadline.
- Asks for ACV or salvage value when they are missing instead of guessing, and cites every input in a tool-inputs table. The code computes; a model, when loaded, only explains.

## Example

Claim #2026-55912 (Elena Rostova), prompt *"Is this a total loss? ACV $14,000, salvage $2,500"*:

> **Result: TOTAL LOSS**
>
> 1. FL rule: threshold is 80% of ACV (SOP_Total_Loss_Valuation.pdf, section 1).
> 2. Repair cost: $11,940.00 original at the SOP labor caps + $0.00 supported supplements = $11,940.00.
> 3. Threshold: 80% of ACV $14,000.00 = $11,200.00, compared with repair cost $11,940.00.
> 4. Repair cost is $740.00 over the threshold.
>
> If the owner keeps the vehicle: ACV less salvage value = $11,500.00 before the deductible.
>
> Valuation check: Kelley Blue Book is a secondary source (historical reference only) and does not count toward the ACV. ACV needs at least two independent valuation sources; 1 acceptable source(s) on file.

It also catches that the shop total ($12,500.00) is $560.00 more than the itemized lines, and lists the title, storage ($45/day, 3 calendar days after written notice) and 3-business-day salvage transfer steps, each cited.

## How it is tested

- **Unit tests:** [`test_total_loss_explainer.py`](../../tests/test_total_loss_explainer.py), 18 test functions: state rules, supplements that do and don't flip the result, missing ACV or salvage, valuation-source counting.
- **Golden eval cases**, scored 2026-10-21 (context precision / recall / faithfulness / reference-fact coverage):
  - `rollover-total-loss-threshold`: 1.00 / 1.00 / 0.71 / 0.67. When does rollover roof damage make a vehicle a structural total loss?
  - `salvage-owner-retention`: 0.75 / 1.00 / 0.63 / 0.40. What happens to the settlement and title if the policyholder keeps the salvage? This is the weakest answer in the mode: retrieval finds the source, but the answer covers less than half of the reference.
- **Eval gap:** there is no claim-scoped golden question that runs the button end to end yet. The golden cases test the underlying rules; the button output is covered by the unit tests.

## Limits

- State rules, valuation methodology, storage caps and claims are synthetic.
- The example shows the simulation path (no model loaded), where every number comes from [`backend/claims_tools.py`](../../backend/claims_tools.py).
- Advisory only: the output is not a coverage or claim decision. The adjuster declares the total loss and chooses the title brand.

## Where it fits

Valuation and total-loss teams at carriers, and valuation products such as CCC or Mitchell. The fit is the explanation layer: a cited, step-by-step reason an adjuster, supervisor or policyholder can check.
