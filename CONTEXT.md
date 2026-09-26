# AutoClaimsRAG — ICM Routing

## What do you want to do?

| Task | Go to | Load first |
|---|---|---|
| Understand the project | `README.md`, `IDENTITY.md` | `docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md` |
| Add or change behavior | `backend/`, `frontend/` | Approved spec and implementation plan |
| Change retrieval behavior | `backend/rag_engine.py`, `backend/agentic_router.py` | `eval/` and retrieval tests |
| Change LLM / inference behavior | `backend/llm_client.py`, `backend/agentic_router.py` | `tests/test_llm_client.py`, `tests/test_llm_stream.py`, `tests/test_agentic_router_llm.py` |
| Change streaming, context caps, or prompt-injection delimiting | `backend/agentic_router.py` (`_assemble_context`, `_online_pipeline`, `run_query_stream`), `backend/app.py` (`/api/chat/stream`) | `tests/test_router_context.py`, `tests/test_prompt_injection.py`, `tests/test_router_stream.py`, `tests/test_api_chat_stream.py`, `tests/test_api_ttf.py` |
| Change API contracts | `backend/app.py` | Contract tests and foundation spec |
| Change the answer guard (withheld outcomes / contacts) | `backend/answer_guard.py`, `backend/api.py` (`guard_result`) | `docs/adversarial-evaluation.md` fix 1, `tests/test_answer_guard.py`, `tests/test_api_answer_guard.py`, `eval/golden_guard_check.py` |
| Change the conflict check (`conflicting_evidence`) | `backend/conflict_check.py`, `backend/contracts.py`, `backend/api.py` (`conflict_result`) | `docs/adversarial-evaluation.md` fix 2, `tests/test_conflict_check.py`, `tests/test_api_conflict_check.py` |
| Change prompt-injection defenses | `backend/prompt_defense.py`, `backend/agentic_router.py` (`_assemble_context`) | `docs/adversarial-evaluation.md` fix 3, `eval/prompt_defense_ab.json`; re-run the held-out set, not only the known one |
| Run or extend the adversarial suite | `eval/adversarial/`, `eval/run_adversarial_eval.py` | `docs/agent-work/adversarial-eval/SPEC.md` (case contract), `docs/adversarial-evaluation.md` |
| Change auth, RBAC, or rate limits | `backend/authn.py`, `backend/rbac.py`, `backend/rate_limit.py` | `SECURITY.md`, `tests/test_authn.py`, `tests/test_api_auth.py`, `tests/test_rbac.py`, `tests/test_api_rbac.py`, `tests/test_rate_limit.py`, `tests/test_api_limits.py` |
| Run unit tests | `tests/` | `README.md` |
| Run retrieval parity | `eval/parity_runner.py` | `docs/enterprise-migration.md` |
| Run foundation gates | `scripts/run_foundation_gates.py` | `stages/verify/CONTEXT.md` |
| Run / check CI | `.github/workflows/tests.yml` | `docs/operations/local-and-production.md` |
| Build or run the Docker image | `Dockerfile`, `docker-compose.yml` | `docs/operations/local-and-production.md` (Docker section) |
| Run or extend the retrieval load test | `scripts/run_retrieval_load_test.py`, `scripts/seed_retrieval_load_corpus.py`, `scripts/locustfile_retrieval.py` | `docs/superpowers/specs/2026-09-02-phase6-load-test-design.md`, `docs/enterprise-migration.md` Phase 6.1 status (batching implemented, authoritative Postgres/HTTP A/B still open) |
| Tune reranker candidate pool / model | `config.py` (`rerank_candidate_pool`, `reranker_model`) | `docs/enterprise-migration.md` Phase 6.1 follow-up — read before changing the pool default again: a quick golden-query check already missed a real regression once; always verify with the full `eval/run_eval.py` suite, not a spot check |
| Read prior context: completed phases, past failures, session history | `docs/build-history.md` | Read before repeating an approach — it holds the Debugging History that `CLAUDE.md` used to carry |
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
