# ICM Verify Stage

## Inputs

- Implemented diff and Act report.
- Approved spec acceptance criteria.
- Test fixtures, golden queries, parity corpus, and risk controls.

## Process

Run focused tests first, then the full pytest suite, retrieval parity/evaluation, security and dependency checks, migration checks, and relevant operational checks. Compare actual output against explicit thresholds and report failures with evidence.

## Checkpoints

- Evidence-required synthesis and refusal behavior pass.
- Claim and tenant isolation pass.
- Input, path, URL, and upload limits pass.
- API contracts and decision boundary remain valid. The answer guard keeps asserted claim outcomes
  consistent with the claim record (`tests/test_answer_guard.py`, `tests/test_api_answer_guard.py`).
- Adversarial regression: for changes to prompts, `backend/answer_guard.py`, `backend/conflict_check.py`
  or `backend/prompt_defense.py`, compare `eval/run_adversarial_eval.py` (known and held-out) and
  `eval/golden_guard_check.py` against `docs/adversarial-evaluation.md` and `eval/prompt_defense_ab.json`.
- Documentation and configuration match the implementation.

## Audit

Record exact commands, exit codes, relevant output, commit/diff range, thresholds, and any advisory findings. A green local result does not imply remote CI or merge completion.

## Outputs

A verification report suitable for human review and the release checklist.
