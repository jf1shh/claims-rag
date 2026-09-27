# ClaimsRAG Enterprise Foundation Design

**Date:** 2026-08-28
**Status:** Draft for review
**Scope:** Production foundation and phased roadmap for an open-source, large-scale deployment

## Goal

Make ClaimsRAG installable, secure-by-default, observable, and ready for production data-plane scaling while preserving its local-first developer experience and existing retrieval behavior.

ClaimsRAG is a claims research and document-grounding system. It helps handlers find and verify evidence from policy and claim documents; it does not independently make coverage, fraud, or payment decisions.

## Current system

The repository is a Python application with:

- FastAPI backend and vanilla JavaScript frontend.
- SQLite-backed hybrid retrieval using dense embeddings, FTS5, reciprocal-rank fusion, and optional cross-encoder reranking.
- Local sentence-transformer embeddings and LM Studio-compatible generation.
- Synthetic seed data only.
- A `VectorStore` abstraction already extracted in `backend/rag_engine.py`.
- Unit tests, a golden evaluation set, and a retrieval parity harness.
- An existing enterprise migration document covering SQLite to Postgres/pgvector, S3, asynchronous ingestion, tenancy, authentication, audit, serving, and compliance.

The current application is explicitly a single-user local tool. It does not yet provide authentication, multiple concurrent writers, durable object storage, tenant isolation, asynchronous ingestion, or production operations.

## Design principles

1. **Evidence before answer.** The system must refuse to synthesize an answer when no supporting evidence is available.
2. **Human decision authority.** Outputs are assistive research and must not be represented as autonomous adjudication.
3. **Tenant isolation by construction.** Tenant context belongs in data models and query boundaries before real tenants are introduced.
4. **Stable seams.** Storage, blob storage, inference, auditing, and configuration must be replaceable behind explicit interfaces.
5. **Local mode remains useful.** A developer can run the project without cloud credentials or a hosted model.
6. **Provider neutrality.** Production deployments may use AWS or compatible alternatives; cloud-specific code stays behind adapters.
7. **Measured retrieval.** Backend replacement and model changes require parity or evaluation evidence.
8. **Secure defaults.** Limits, validation, restricted origins, safe downloads, and non-leaking errors apply by default.
9. **Incremental migration.** Each phase produces a usable, testable system and avoids a big-bang rewrite.

## Foundation architecture

```text
                  Browser / API Client
                           |
                 FastAPI application layer
       validation · auth seam · tenant context · audit
                           |
       +-------------------+-------------------+
       |                   |                   |
 Retrieval service   Claims/workflow API   Health/metrics
       |                   |
   VectorStore       domain repositories
       |                   |
 SQLite backend   Postgres/pgvector backend
       |
 DocumentBlobStore
       |
 Local filesystem     S3-compatible storage
```

The application layer owns HTTP contracts, request validation, authorization boundaries, request IDs, and audit event creation. Domain services own ingestion, retrieval orchestration, and claim context. Infrastructure adapters implement SQLite/Postgres retrieval, filesystem/S3 blobs, and local/remote model providers.

### Required interfaces

The existing `VectorStore` interface remains the retrieval boundary. Add equivalent boundaries for:

```python
class DocumentBlobStore(ABC):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def create_download_url(self, key: str, expires_seconds: int) -> str: ...

class AuditSink(ABC):
    def record(self, event: dict) -> None: ...
```

Model/provider configuration must also be injectable rather than allowing request data to determine a target URL. The current engine allowlist and SSRF protection remain mandatory.

## Foundation scope

### F6. Harness controls and ICM navigation

ClaimsRAG will adopt the useful governance patterns from the other repositories without copying their frontend-specific tooling. The implementation is Python/FastAPI, so runtime contracts use Pydantic, tests use pytest, and the harness is a deterministic Python or cross-platform command-line layer.

ICM is an additive navigation and evidence layer, not a second application framework. It must point to authoritative artifacts rather than duplicate them.

Recommended repository layer:

```text
IDENTITY.md
CONTEXT.md
_config/
  conventions.md
  glossary.md
  risk-controls.md
stages/
  sense/CONTEXT.md
  propose/CONTEXT.md
  act/CONTEXT.md
  verify/CONTEXT.md
  learn/CONTEXT.md
```

Stage contracts:

- **Sense:** inspect the approved spec, repository state, test results, dependency/security reports, evaluation results, and migration state; produce findings with file/line or command evidence.
- **Propose:** convert findings into bounded work orders containing risk, affected contracts, acceptance criteria, and verification commands; do not implement.
- **Act:** implement only from an approved spec/plan, preserve interfaces, and record changed artifacts.
- **Verify:** run unit/API tests, retrieval evaluation/parity, security checks, migration checks, and relevant load checks; report actual output.
- **Learn:** record incidents and reusable lessons; promote mechanically detectable lessons into self-tested guardrails, starting as advisory sensors and becoming blocking only after they demonstrate regression value.

The harness should initially provide these controls:

1. Spec presence and spec-ordering sensor for logic changes.
2. Pydantic contract coverage and boundary-validation checks.
3. Unit-test coverage checks for parser, chunking, storage, tenancy, retrieval, and security modules.
4. Retrieval golden-set and parity checks.
5. Cross-claim and cross-tenant isolation checks.
6. Secret scanning and dependency vulnerability auditing.
7. Static security analysis.
8. Migration reproducibility and schema-version checks.
9. Diff-size and sensitive-infrastructure touch sensors.
10. Instruction/workflow tamper detection.
11. Documentation freshness checks for public behavior changes.
12. Guardrail self-tests and lesson-to-guardrail traceability.

No new control becomes a blocking merge gate merely because it exists. It starts as an advisory sensor, is measured against repository history, and is promoted by a reviewed change once false positives and backlog behavior are understood. Security leaks, tenant isolation failures, broken migrations, and missing evidence grounding are exceptions: they are blocking from the first production release because their failure modes are unacceptable.

### F7. Interpretable Context Methodology contract

Every chat/search result that can influence a claims workflow must expose three distinct layers:

```text
Evidence → Interpretation → Decision boundary
```

#### Evidence

Evidence is the exact material the system used to ground the response:

- Stable document ID and document version.
- Tenant ID and optional claim ID, never exposed to unauthorized callers.
- Source filename and content type.
- Source checksum or version identifier.
- Parent/chunk ID.
- Character/page/row offsets when available.
- Exact retrieved excerpt.
- Retrieval method and rank/score.
- Retrieval timestamp.

Evidence is immutable for the lifetime of a response. A citation must resolve to the stored document version and excerpt that were actually supplied to the model.

#### Interpretation

Interpretation is the system's explanation of the evidence:

- Answer text.
- Evidence IDs used for each material assertion where feasible.
- Calculations with explicit operands and results.
- Assumptions and missing facts.
- Conflicts between sources.
- Confidence or uncertainty label describing evidence completeness, not a claim of statistical certainty.
- Refusal reason when evidence is missing, contradictory, stale, or unauthorized.

Interpretation must never silently introduce facts that are absent from Evidence. Model-generated content is untrusted until it passes grounding and citation validation.

#### Decision boundary

Decision is owned by a human or an external claims system:

- `decision_status`: `not_a_decision`, `recommended_for_review`, or `human_recorded`.
- The assistant must default to `not_a_decision`.
- Coverage, fraud, reserve, payment, denial, and referral outcomes require human review and must not be represented as completed decisions by the assistant.
- If a future integration records a human decision, it must identify the actor, timestamp, reason, and evidence snapshot separately from the assistant interpretation.

Canonical response shape:

```json
{
  "request_id": "req_...",
  "answer": {
    "text": "...",
    "status": "grounded|insufficient_evidence|conflicting_evidence|error",
    "interpretation": {
      "claims": [
        {
          "text": "...",
          "evidence_ids": ["ev_1"],
          "calculation": {
            "operands": ["2400", "3500"],
            "operation": "sum",
            "result": "5900"
          }
        }
      ],
      "assumptions": [],
      "uncertainty": "low|medium|high|not_assessed"
    },
    "decision_boundary": {
      "decision_status": "not_a_decision|recommended_for_review|human_recorded",
      "human_action_required": true
    }
  },
  "evidence": [
    {
      "id": "ev_1",
      "document_id": "doc_...",
      "document_version": "sha256:...",
      "filename": "receipt.xlsx",
      "chunk_id": "chunk_...",
      "excerpt": "...",
      "locator": {"page": 1, "start_char": 0, "end_char": 120},
      "retrieval": {"method": "hybrid_rerank", "rank": 1, "score": 0.91}
    }
  ],
  "pipeline": {
    "embedding_model": "...",
    "reranker_model": "...",
    "generation_model": "...",
    "retrieved_at": "..."
  }
}
```

The initial compatibility layer may continue returning the current `answer`, `sources`, and `pipeline_logs` fields, but new consumers should use the structured fields. A compatibility test must ensure every returned source has a stable evidence ID and that the legacy source list is a projection of `evidence`, not an independently assembled list.

ICM checkpoints for a grounded answer are:

1. **Scope checkpoint:** active tenant, claim, authorization, and query scope resolved.
2. **Retrieval checkpoint:** evidence exists, is authorized, and is versioned.
3. **Interpretation checkpoint:** claims, calculations, assumptions, and conflicts are represented.
4. **Decision checkpoint:** response explicitly states whether it is advice or a human-recorded decision.
5. **Audit checkpoint:** request ID, actor, evidence IDs, model versions, and outcome are recorded without unnecessary sensitive content.

### F8. Prioritized release gate matrix

| Priority | Gate | Initial mode | Blocking from first production release? | Evidence |
|---|---|---|---|---|
| P0 | Secret scanning on changed and full repository content | Blocking | Yes | Scanner output and zero exposed credentials |
| P0 | Cross-tenant and cross-claim isolation | Blocking | Yes | Automated negative-access tests |
| P0 | Evidence-required synthesis/refusal | Blocking | Yes | Zero-context and unsupported-claim tests |
| P0 | Upload/path/URL/input boundary validation | Blocking | Yes | Malformed input, traversal, SSRF, size-limit tests |
| P0 | Migration reproducibility and schema compatibility | Blocking | Yes | Empty-database migration and upgrade tests |
| P0 | Dependency vulnerability audit | Advisory initially; critical/high policy blocking | Yes for critical/high findings | `pip-audit` report and reviewed exceptions |
| P0 | API authentication/tenant-context enforcement seam | Blocking for production profile | Yes | Protected-route matrix |
| P1 | Unit/API test suite and coverage of risk-bearing modules | Blocking | Yes | Pytest output plus coverage report |
| P1 | Retrieval golden-set regression | Blocking after baseline is recorded | Yes | Recall/precision/groundedness thresholds |
| P1 | SQLite/Postgres retrieval parity | Advisory until Postgres exists; blocking at migration | No initially | Parity report with tolerance |
| P1 | Static security analysis | Advisory initially; blocking for high-confidence findings | Yes for high-confidence critical findings | Bandit/SAST output |
| P1 | Structured logs and request IDs | Blocking | Yes | Integration tests and sample logs |
| P1 | Health/readiness checks | Blocking | Yes | Dependency failure tests |
| P1 | Upload/ingestion idempotency and failure recovery | Advisory in sync mode; blocking in async mode | At async release | Replay and failure-drill results |
| P2 | Spec-ordering sensor | Advisory initially | No | Diff report and false-positive history |
| P2 | Documentation freshness sensor | Advisory initially | No | Changed behavior/doc report |
| P2 | Instruction/workflow tamper sensor | Advisory initially | No | Diff report |
| P2 | Diff-size and infrastructure containment sensors | Advisory initially | No | PR metadata/report |
| P2 | Lesson-to-guardrail traceability | Advisory initially; blocking once guardrails are introduced | At harness release | Self-test and traceability report |
| P2 | Load/performance thresholds | Advisory during foundation; blocking at scale cutover | At scale cutover | Reproducible load-test report |
| P2 | Mutation testing for critical safety paths | Advisory | No | Mutation score and surviving-mutant review |

Gate policy:

- A gate must produce reproducible output and identify the exact artifact or command that failed.
- Blocking gates must have self-tests or fixture-driven tests that prove both failure and success paths.
- Advisory sensors must not be used as a hidden merge veto.
- Exceptions require a written reason, owner, expiration/review date, and link to the relevant risk.
- The CI workflow must run the same authoritative commands documented for local verification.

### F0. Repository and release hygiene

Deliverables:

- Cross-platform installation instructions for Linux, macOS, and Windows.
- Explicit supported Python version and dependency policy.
- `.env.example` documenting every supported setting without secrets.
- Development, test, evaluation, and production-start commands.
- Clear distinction between synthetic demo data and customer data.
- CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, release/versioning guidance, and issue templates where missing.
- Ignore rules for local databases, uploaded documents, model caches, generated files, and secrets.
- Documentation of third-party model and dependency licenses.

Acceptance criteria:

- A new developer can install dependencies, run tests, start local mode, and understand where data is stored without inspecting source code.
- A deployment operator can identify required production configuration and which settings are development-only.
- No generated customer-like data or local secrets are required to run CI.

### F1. Configuration and application lifecycle

Centralize configuration with typed environment-backed settings. Configuration must cover:

```text
APP_ENV
LOG_LEVEL
CORS_ORIGINS
RAG_DB_PATH
STORED_DOCUMENTS_DIR
OBJECT_STORAGE_PROVIDER
OBJECT_STORAGE_BUCKET
EMBEDDING_MODEL
EMBEDDING_DIMENSIONS
RERANKER_MODEL
LLM_PROVIDER
LLM_BASE_URL
LLM_MODEL
MAX_UPLOAD_BYTES
MAX_DOCUMENT_CHARS
MAX_QUERY_CHARS
MAX_TOP_K
REQUEST_TIMEOUT_SECONDS
SIMULATION_MODE
```

Requirements:

- Paths are repository-anchored or explicitly configured.
- Production startup fails with actionable messages for invalid required settings.
- Development defaults do not silently apply in production.
- Application construction is testable; importing the module must not require model downloads or unavailable external services.
- Health endpoints distinguish process liveness from dependency readiness.
- Local model unavailability produces a useful status and does not crash unrelated API routes.
- Simulation mode is explicit and disabled by default outside development/test.

Endpoints:

- `GET /health/live`: process is running.
- `GET /health/ready`: required configured dependencies are usable.
- Existing `/api/status` remains a development-facing diagnostic endpoint or is adapted to the new health model.

### F2. Storage interfaces and migration-safe data model

Keep SQLite as the local backend. Add a production-compatible schema and adapter without changing caller behavior.

Every tenant-owned record must eventually contain `tenant_id`, including:

- Documents.
- Parent chunks.
- Child chunks.
- Claims.
- Ingestion jobs.
- Audit events.

Document metadata must include:

- Stable document ID.
- Tenant ID.
- Optional claim ID.
- Filename and normalized storage key.
- File type and byte size.
- Content checksum.
- Embedding model ID and vector dimensions.
- Ingestion status.
- Created and updated timestamps.
- Deletion/retention metadata.

Production constraints:

- Unique document identity is scoped to `(tenant_id, claim_id, filename)`.
- Foreign keys prevent orphaned chunks and job records.
- Query methods require explicit tenant scope in production code.
- Postgres Row-Level Security is applied before live multi-tenant data is accepted.
- Embedding model changes are versioned and never silently mixed in one index.

Local mode may use a deterministic default tenant such as `local-development`, but that default must not be accepted as a production identity.

### F3. API contracts and security baseline

Introduce stable request/response schemas for upload, claim upload, search, chat, document listing, document content/download, deletion, job status, and health.

All request boundaries enforce:

- Maximum upload bytes.
- Maximum extracted document characters.
- Maximum query characters.
- Bounded `top_k`.
- Supported file extensions and content types.
- Normalized filenames and safe storage keys.
- Safe response headers for downloads.
- Request IDs propagated to logs and errors.
- Generic production error responses without stack traces or internal exception messages.
- Explicit configured CORS origins.
- SSRF-safe model/provider targets.

The raw `/api/eval/search` endpoint is development/CI-only and must be disabled or protected in production.

Authentication is a foundation seam: local development may use an explicit development identity, but production routes must be designed to receive a verified principal and tenant context. No route may infer tenant identity from a client-controlled filename, claim ID, or query parameter.

### F4. Ingestion contract

Define a durable ingestion job model even while the initial implementation is synchronous:

```text
queued -> parsing -> embedding -> indexed
                         |          |
                       failed     deleted
```

Each job includes:

- `job_id`.
- Tenant ID.
- Document ID.
- Claim ID.
- Idempotency key.
- Status and progress.
- Error code and safe error message.
- Retry count.
- Created, started, completed, and updated timestamps.

The initial upload implementation may finish synchronously, but its response contract must be compatible with a future `202 Accepted` response containing `job_id`. A later asynchronous worker must be able to retry safely and never expose a partially indexed document as complete.

### F5. Evaluation and release gates

CI and release checks must cover:

- Existing unit tests for parser, chunking, storage, scoping, and routing.
- Retrieval parity harness.
- Golden-query regression evaluation.
- Zero-context refusal behavior.
- Cross-claim isolation.
- Cross-tenant isolation once tenant context exists.
- Upload limits and malformed-file behavior.
- API contract behavior.
- Basic retrieval and ingestion load tests.
- Supported Python/platform matrix.
- Formatting, static checks, dependency vulnerability scanning, and secret scanning.

Minimum release guarantees:

- No ungrounded answer when no evidence exists.
- No cross-claim or cross-tenant document leakage.
- No critical security findings.
- Migrations reproduce the schema from an empty database.
- Golden retrieval quality does not regress beyond an explicitly configured tolerance.

## Phased enterprise roadmap

### Phase 1 — Production data plane

- Add Alembic migrations for Postgres.
- Implement `PostgresVectorStore` with pgvector cosine search and Postgres full-text search.
- Add tenant-aware query predicates and RLS policies.
- Preserve RRF and reranking behavior.
- Add SQLite-to-Postgres migration with counts, checksums, embedding dimensions, and orphan checks.
- Run the parity harness across both backends.

Exit criteria:

- A demo corpus migrates with matching counts and verified checksums.
- Tenant B cannot retrieve tenant A's documents at the database or API layer.
- Golden-query parity meets the agreed recall tolerance.

### Phase 2 — Durable document storage

- Implement S3-compatible blob storage with an adapter.
- Store objects under tenant and claim scope.
- Enable encryption, versioning, and lifecycle/retention configuration.
- Add presigned downloads with authorization checks.
- Keep the API stateless with respect to uploaded files.
- Preserve per-scope overwrite and deletion semantics.

Exit criteria:

- Source files survive API process replacement.
- Unauthorized users cannot obtain document content or download URLs.
- Deletion behavior is documented and tested for both metadata and objects.

### Phase 3 — Asynchronous ingestion

- Add queue and worker interfaces.
- Change upload to return `202` and `job_id` when async mode is enabled.
- Parse, chunk, batch-embed, and incrementally upsert in workers.
- Add idempotency, retry/backoff, dead-letter handling, and orphan detection.
- Add `GET /api/jobs/{job_id}` and frontend progress binding.
- Ensure failed jobs cannot leave a document in an apparently indexed state.

Exit criteria:

- Ingestion no longer rebuilds an entire in-memory corpus per write.
- Replaying a job produces one consistent document version.
- Failure drills show recoverable jobs and no orphaned searchable content.

### Phase 4 — Identity, tenancy, and governance

- Add OIDC/SSO and service accounts.
- Require verified tenant context on every protected route.
- Implement roles: adjuster, supervisor, SIU, and administrator.
- Add claim-level access control.
- Add immutable audit records for upload, replacement, deletion, chat, search, download, and authorization failures.
- Record actor, tenant, claim, request ID, sources returned, timestamps, and outcome without storing unnecessary sensitive content.
- Add rate limits, quotas, and administrative retention/deletion workflows.

Exit criteria:

- Permission matrix is covered by automated tests.
- Every protected action has an audit event.
- Abuse tests return bounded `413` or `429` responses rather than consuming unbounded resources.

### Phase 5 — Production inference

- Add an explicit OpenAI-compatible provider abstraction.
- Support self-hosted vLLM/TGI and configured hosted endpoints without allowing arbitrary request URLs.
- Move reranking to a batched service when load requires it.
- Add streamed or asynchronous chat responses.
- Cap context assembly by dossier and global-match budgets.
- Version prompts, embedding models, rerankers, and generation models.
- Add latency, timeout, failure, and model-availability metrics.

Exit criteria:

- Planner and synthesis work through a replaceable provider adapter.
- Streaming/async behavior remains grounded and preserves source IDs.
- Production concurrency does not cause unbounded request-thread blocking.

### Phase 6 — Scale, drift, compliance, and cutover

- Load test at 100,000+ documents across multiple tenants.
- Tune HNSW and monitor recall against brute-force/parity samples.
- Schedule tenant-specific retrieval and answer-quality evaluations.
- Alert on retrieval, faithfulness, latency, and error regressions.
- Implement backups, restore drills, disaster recovery, and incident response runbooks.
- Implement retention, deletion, access reviews, and legal hold processes.
- Produce a SOC 2-style evidence pack where applicable.
- Use feature flags, staging parity checks, blue/green deployment, and rollback drills.

Exit criteria:

- Documented performance targets are met under representative load.
- Restore and rollback procedures are tested, not merely documented.
- Production cutover can be reversed within the agreed recovery window.

## Recommended execution order

1. Foundation repository/configuration/API/security work.
2. Postgres/pgvector data plane with tenant fields and RLS.
3. Durable object storage.
4. Asynchronous ingestion.
5. Identity, authorization, and audit.
6. Production model serving and streaming.
7. Scale testing, drift monitoring, compliance operations, and cutover.

This order puts tenant boundaries into the schema before real tenant data, removes the single-process storage bottleneck before horizontal scaling, and postpones infrastructure complexity until the interfaces and release gates are in place.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Approximate HNSW search loses relevant passages | Maintain parity harness; tune and monitor recall before migration. |
| Embedding model changes invalidate vectors | Store model ID/dimensions; re-embed through versioned jobs. |
| LLM latency varies under concurrency | Stream or queue requests; measure time-to-first-token and total latency. |
| Sensitive claims data is exposed | Tenant context, RLS, authorization before retrieval, encrypted storage, audit logs, and safe downloads. |
| Prompt injection in uploaded documents | Treat source text as untrusted evidence; delimit it; never let it control tools, URLs, or authorization. |
| Regulatory expectations exceed the prototype | Keep human decision authority explicit; involve company compliance/legal review before production claims use. |
| Open-source users misconfigure deployment | Validate production settings, provide secure defaults, publish deployment profiles, and fail fast on unsafe combinations. |

## Out of scope for the foundation

- Autonomous claim approval, denial, fraud referral, or payment authorization.
- Direct integration with a carrier's core claims system.
- Production SSO provider selection.
- A mandatory cloud vendor.
- A managed vector database requirement.
- Full compliance certification.
- Replacing the current frontend with a new framework.

These may be addressed in later phases or by adopters through documented adapters.

## Open decisions for implementation planning

The foundation implementation should preserve these choices until they are explicitly decided during planning:

1. Whether typed settings use a new dependency or a small in-repository configuration layer, based on the project's dependency policy.
2. Which Postgres migration tool and S3-compatible SDK best fit the supported Python versions.
3. Whether the first production profile targets AWS specifically or any S3-compatible provider.
4. Whether local development exposes a development identity through headers, a CLI-generated token, or a disabled-auth profile.
5. The exact performance SLOs for retrieval, ingestion, and time-to-first-token.

These are implementation details, not blockers for the foundation architecture.
