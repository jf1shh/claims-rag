# Contributing to AutoClaimsRAG

## Before changing code

1. Read `IDENTITY.md` and `CONTEXT.md`.
2. Read the approved design in `docs/superpowers/specs/` and the relevant plan task.
3. Read `CLAUDE.md` and `SECURITY.md` when touching routes, storage, providers, or uploaded content.
4. Write a failing test for the behavior being changed.
5. Keep changes scoped to the approved task.

## Verification

Run focused tests first, then:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python eval/parity_runner.py
```

Run the foundation gate command when it exists. Report the commands actually run and their output; do not claim CI is green based on local intent.

## Pull requests

- Explain the user-facing or operational reason for the change.
- Link the relevant specification and plan task.
- Include security and privacy impact.
- Identify new or changed API/data contracts.
- Include test and evaluation evidence.
- Call out any changed model, embedding, prompt, migration, or tenant behavior.
- Do not include secrets, proprietary claim documents, generated databases, or uploaded files.
- A human reviews and merges pull requests; contributors and coding agents do not self-merge.
