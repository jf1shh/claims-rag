# ICM Learn Stage

## Inputs

- Verification failures, incidents, reviewer findings, and production observations.
- Existing lessons, guardrails, and risk controls.

## Process

Record the failure mode, impact, root cause, evidence, and prevention. If the lesson is mechanically detectable, add a self-tested guardrail; begin it as an advisory sensor unless the risk-control matrix makes it blocking from the first production release.

## Checkpoints

- The lesson is specific enough to prevent recurrence.
- A guardrail has known-bad and known-good tests.
- A guardrail references its motivating lesson and the lesson references the test.
- No rule weakens a higher-priority security or grounding control.

## Audit

Record the incident or review evidence, lesson file, guardrail ID, self-test, promotion decision, owner, and review date for exceptions.

## Outputs

A lesson, a self-tested guardrail, an advisory finding, or a documented decision not to automate the control.
