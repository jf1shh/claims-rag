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
.venv/bin/python scripts/run_foundation_gates.py --mode gate
```

Report the commands actually run and their output; do not claim CI is green based on local intent.

## Pull requests

- Explain the user-facing or operational reason for the change.
- Link the relevant specification and plan task.
- Include security and privacy impact.
- Identify new or changed API/data contracts.
- Include test and evaluation evidence.
- Call out any changed model, embedding, prompt, migration, or tenant behavior.
- Do not include secrets, proprietary claim documents, generated databases, or uploaded files.
- This is a solo-maintained repository where the maintainer directs an AI coding agent for most implementation work (see CLAUDE.md's Session Log for the honest split of what's agent-driven vs. maintainer-directed). Once a change is implemented, verified (tests/lint/foundation-gate green), and documented, the agent opens the PR, watches CI on the self-hosted runner, and merges it as a standing, maintainer-authorized workflow step — there is no separate second human reviewer, and none is claimed. Any external contributor's PR is reviewed and merged by the maintainer.
