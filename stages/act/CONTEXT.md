# ICM Act Stage

## Inputs

- Approved work order or implementation-plan task.
- Relevant source files, contracts, fixtures, and lessons.
- Required verification commands.

## Process

Write a failing test first, implement the smallest change that satisfies the approved behavior, and preserve existing interfaces unless the spec explicitly changes them. Keep evidence, interpretation, and decision data separate in claims-facing behavior.

## Checkpoints

- Confirm the change matches the approved spec.
- Confirm untrusted input is validated at the boundary.
- Confirm no source, claim, tenant, or provider scope is widened accidentally.

## Audit

Record changed files, tests added, commands run, observed output, and any unresolved concerns. Do not self-merge or treat an unrun command as verification.

## Outputs

A reviewable change with tests and a report for `stages/verify/CONTEXT.md`.
