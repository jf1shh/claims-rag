# AutoClaimsRAG — Identity

AutoClaimsRAG is a local-first, evidence-grounded research assistant for auto insurance claims. It uses synthetic data in this repository and must not be described as an autonomous claims adjudicator.

## Workspace map

| Area | Purpose |
|---|---|
| `backend/` | FastAPI application, retrieval engine, router, and domain services |
| `frontend/` | Browser UI for claims, documents, chat, and pipeline traces |
| `tests/` | Unit and integration tests for parsing, retrieval, scoping, and API behavior |
| `eval/` | Golden queries, retrieval parity, and answer-quality evaluation |
| `docs/` | Approved specifications, plans, migration and operational documentation |
| `docs/build-history.md` | Completed phases 1-18, Debugging History, and the full session log — moved out of `CLAUDE.md` 2026-09-05; not loaded by default |
| `alembic/` | Postgres schema migrations (`alembic.ini` at root); `0001_initial_enterprise_schema.py` defines tenant_id/RLS/HNSW |
| `assets/` | Static images and demo media referenced by README and the frontend |
| `_config/` | Layer 3 — shared conventions, glossary, and risk controls |
| `.github/` | CI workflows (`tests.yml`) and the advisory PR-review workflow |
| `scripts/` | Developer and release automation |
| `sample_guidelines/` | Synthetic seed documents only |
| `stored_documents/` | Local runtime data; never commit customer or generated files |
| `rag_store.db` | Local SQLite runtime database; never commit |
| `stages/` | ICM navigation contracts for engineering work |

## Authoritative sources

1. Approved design: `docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md`
2. Implementation plan: `docs/superpowers/plans/2026-08-28-enterprise-foundation-plan.md`
3. Enterprise migration: `docs/enterprise-migration.md`
4. Test and evaluation commands: `README.md` and `.github/workflows/tests.yml`

ICM files route contributors to these artifacts; they do not replace them.
