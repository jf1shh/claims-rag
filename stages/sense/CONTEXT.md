# ICM Sense Stage

## Inputs

- Approved design and implementation plan.
- Repository diff and current branch state.
- Test, evaluation, security, dependency, and migration output.
- Existing lessons and risk controls.

## Process

Inspect the evidence before proposing work. Identify missing tests, contract drift, security findings, retrieval regressions, documentation drift, and operational risks. Every finding names the command, file, line, fixture, or metric that supports it.

## Checkpoints

- Confirm the approved spec and plan are the current authority.
- Confirm findings are scoped to the repository and current change.
- Distinguish blocking failures from advisory sensors.

## Audit

Record the scan command, commit or diff range, findings, severity, and evidence paths. Do not claim a check passed without observed output.

## Outputs

A deterministic findings report consumed by `stages/propose/CONTEXT.md` and the foundation gate runner.
