# AutoClaimsRAG — ICM Routing

## What do you want to do?

| Task | Go to | Load first |
|---|---|---|
| Understand the project | `README.md`, `IDENTITY.md` | `docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md` |
| Add or change behavior | `backend/`, `frontend/` | Approved spec and implementation plan |
| Change retrieval behavior | `backend/rag_engine.py`, `backend/agentic_router.py` | `eval/` and retrieval tests |
| Change API contracts | `backend/app.py` | Contract tests and foundation spec |
| Change auth, RBAC, or rate limits | `backend/authn.py`, `backend/rbac.py`, `backend/rate_limit.py` | `SECURITY.md`, `tests/test_authn.py`, `tests/test_api_auth.py`, `tests/test_rbac.py`, `tests/test_api_rbac.py`, `tests/test_rate_limit.py`, `tests/test_api_limits.py` |
| Run unit tests | `tests/` | `README.md` |
| Run retrieval parity | `eval/parity_runner.py` | `docs/enterprise-migration.md` |
| Run foundation gates | `scripts/run_foundation_gates.py` | `stages/verify/CONTEXT.md` |
| Run / check CI | `.github/workflows/tests.yml` | `docs/operations/local-and-production.md` |
| Record a new lesson | `stages/learn/CONTEXT.md` | `docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md` |

## Session start

1. Read `IDENTITY.md`.
2. Read the approved design and relevant implementation plan.
3. Read `CLAUDE.md` and repository security guidance before changing code.
4. Identify the affected contract, tests, evaluation fixtures, and documentation.
5. Write a failing test before implementation.

## ICM stages

- `stages/sense/CONTEXT.md`: inspect current evidence and detect drift.
- `stages/propose/CONTEXT.md`: create a bounded, reviewable work order.
- `stages/act/CONTEXT.md`: implement only approved work.
- `stages/verify/CONTEXT.md`: run the authoritative checks.
- `stages/learn/CONTEXT.md`: preserve lessons and promote proven controls.

The approved spec is the source of truth. If a request conflicts with it, stop and update the design before coding.
