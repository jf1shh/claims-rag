# ClaimsRAG Enterprise Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the local ClaimsRAG prototype into a secure, configurable, observable, testable foundation ready for Postgres, object storage, async ingestion, and enterprise tenancy.

**Architecture:** Preserve the existing `VectorStore` retrieval seam and add focused boundaries for configuration, application construction, blob storage, audit events, tenant context, and structured grounded responses. Keep SQLite/filesystem/local-model mode as the hermetic development profile while making production boundaries explicit and migration-safe. Add an ICM navigation layer and a deterministic Python harness whose critical controls are tested and whose new sensors are advisory until proven.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, SQLite for local mode, existing NumPy/RAG stack, GitHub Actions, Python-native CLI scripts. Add dependencies only after verifying the repository’s compatibility and need.

**Spec:** `docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md`

> **Status: COMPLETE (2026-08-28).** All nine tasks are implemented, committed on
> `feat/enterprise-foundation`, and verified: `pytest tests/ -q` → 90 passed / 1 skipped;
> `python scripts/run_foundation_gates.py --mode gate` → 0 findings, 0 blocking;
> `python eval/parity_runner.py` → mean recall@4 = 1.0; `compileall` clean; no runtime
> artifacts or secrets committed. Remaining manual steps: golden evaluation with a live
> LM Studio judge (final checklist item 4) and human diff review before merge (item 17).
> Deferred: dependency audit (`pip-audit`) and static security analysis (bandit) are not
> yet wired into the harness — see the deferred-findings record in
> `docs/enterprise-migration.md`.

## Global Constraints

- Evidence before answer: refuse synthesis when no supporting evidence is available.
- Human decision authority: outputs are assistive research and do not independently make coverage, fraud, reserve, payment, denial, or referral decisions.
- Tenant isolation by construction: tenant context belongs in data models and query boundaries before real tenants are introduced.
- Local mode remains useful without cloud credentials or a hosted model.
- Provider-specific implementations stay behind adapters.
- New controls begin as advisory sensors unless the spec explicitly requires first-release blocking behavior.
- P0 blocking controls are secret scanning, isolation, evidence grounding, input/path/URL validation, migration correctness, production authentication/tenant context, and critical/high dependency security policy.
- Do not expose tenant IDs, claim records, excerpts, or audit details to unauthorized callers.
- Do not commit secrets, generated databases, uploaded documents, model caches, or local runtime state.
- Every new or changed behavior gets a failing pytest test before implementation and a passing focused test before broader verification.

---

## File map

### New files

- `config.py` — typed environment-backed settings and production validation.
- `app_factory.py` — testable FastAPI application construction and dependency wiring.
- `backend/contracts.py` — Pydantic request/response models and structured ICM response models.
- `backend/tenant_context.py` — explicit principal/tenant context abstraction.
- `backend/blob_store.py` — `DocumentBlobStore` interface and local filesystem adapter.
- `backend/audit.py` — `AuditSink` interface and local structured sink.
- `backend/health.py` — liveness/readiness checks.
- `backend/harness.py` — deterministic foundation gate runner and finding model.
- `tests/test_config.py` — settings and validation tests.
- `tests/test_contracts.py` — contract and evidence-model tests.
- `tests/test_tenant_context.py` — principal and tenant-boundary tests.
- `tests/test_blob_store.py` — local blob-store security and consistency tests.
- `tests/test_audit.py` — audit sink tests.
- `tests/test_health.py` — liveness/readiness tests.
- `tests/test_harness.py` — guardrail/sensor self-tests.
- `IDENTITY.md`, `CONTEXT.md`, `_config/conventions.md`, `_config/glossary.md`, `_config/risk-controls.md` — ICM navigation layer.
- `stages/sense/CONTEXT.md`, `stages/propose/CONTEXT.md`, `stages/act/CONTEXT.md`, `stages/verify/CONTEXT.md`, `stages/learn/CONTEXT.md` — stage contracts.
- `.env.example` — documented configuration template.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` — public project governance, when not already present.
- `.github/workflows/foundation.yml` — foundation CI commands.

### Existing files to modify

- `backend/app.py` — use application factory/configuration, typed contracts, tenant context, audit, health, and bounded inputs.
- `backend/rag_engine.py` — preserve `VectorStore`, add tenant-aware method shape and blob abstraction integration without breaking local compatibility.
- `backend/agentic_router.py` — produce structured evidence/interpretation/decision output while preserving legacy response projection.
- `ingest_all.py` — consume centralized configuration and ingestion contract.
- `requirements.txt` — only verified additions for development/runtime gates.
- `pytest.ini` — retain import behavior and add test markers/configuration if needed.
- `README.md` — installation, local mode, production profile, security posture, evaluation, and support boundaries.
- `docs/enterprise-migration.md` — link to the approved foundation design and mark foundation milestones.
- `.gitignore` — protect runtime data and secrets.
- `.github/workflows/tests.yml` — add foundation tests and security/evaluation commands without breaking the existing matrix.

---

## Task 1: Establish ICM navigation and repository governance

**Files:**
- Create: `IDENTITY.md`, `CONTEXT.md`, `_config/conventions.md`, `_config/glossary.md`, `_config/risk-controls.md`
- Create: `stages/sense/CONTEXT.md`, `stages/propose/CONTEXT.md`, `stages/act/CONTEXT.md`, `stages/verify/CONTEXT.md`, `stages/learn/CONTEXT.md`
- Modify: `README.md`, `CLAUDE.md`, `docs/enterprise-migration.md`
- Test: `tests/test_repository_hygiene.py`

**Interfaces:**
- Produces a navigation layer that links to the approved spec, existing migration plan, authoritative tests, and future harness commands.

- [x] **Step 1: Write the failing repository-hygiene tests**

```python
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_given_icm_navigation_files_then_each_points_to_authoritative_artifacts():
    context = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    assert "docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md" in context
    assert "stages/verify/CONTEXT.md" in context


def test_given_all_icm_stages_then_each_has_inputs_process_checkpoints_audit_outputs():
    for stage in ("sense", "propose", "act", "verify", "learn"):
        text = (ROOT / "stages" / stage / "CONTEXT.md").read_text(encoding="utf-8")
        for heading in ("Inputs", "Process", "Checkpoints", "Audit", "Outputs"):
            assert heading in text
```

- [x] **Step 2: Run the focused tests and verify they fail because the ICM files do not exist**

Run: `pytest tests/test_repository_hygiene.py -q`

Expected: FAIL with missing-file or missing-section assertions.

- [x] **Step 3: Create the ICM files**

`IDENTITY.md` maps the repository’s backend, frontend, docs, tests, evaluation, and runtime-data boundaries. `CONTEXT.md` routes contributors to the approved spec, implementation plan, test commands, security documents, and five stage contracts. Each stage contract explicitly names its inputs, process, checkpoints, audit evidence, and output artifact. `_config` files define conventions, domain terms, and risk controls without duplicating the authoritative spec.

- [x] **Step 4: Document the ICM and foundation controls**

Add README sections covering the ICM loop, local versus production profiles, evidence/interpretation/decision semantics, and the rule that ICM is navigation and evidence—not an additional runtime orchestration framework. Link the migration document to the foundation design.

- [x] **Step 5: Run the focused tests**

Run: `pytest tests/test_repository_hygiene.py -q`

Expected: PASS.

- [x] **Step 6: Commit the independently reviewable ICM documentation change**

```bash
git add IDENTITY.md CONTEXT.md _config stages README.md CLAUDE.md docs/enterprise-migration.md tests/test_repository_hygiene.py
git commit -m "docs: add ICM navigation for enterprise foundation"
```

---

## Task 2: Centralize configuration and application construction

**Files:**
- Create: `config.py`, `app_factory.py`
- Create: `tests/test_config.py`, `tests/test_app_factory.py`
- Modify: `backend/app.py`, `backend/rag_engine.py`, `backend/agentic_router.py`, `ingest_all.py`

**Interfaces:**
- `Settings.from_env(environ: Mapping[str, str] | None = None) -> Settings`
- `Settings.validate_for_environment() -> None`
- `create_app(settings: Settings | None = None) -> FastAPI`
- `get_settings() -> Settings`

- [x] **Step 1: Write failing settings tests**

```python
import pytest
from config import Settings


def test_given_empty_development_environment_then_local_defaults_are_explicit():
    settings = Settings.from_env({"APP_ENV": "development"})
    assert settings.app_env == "development"
    assert settings.simulation_mode is True
    assert settings.max_top_k > 0


def test_given_production_without_required_provider_configuration_then_validation_fails():
    settings = Settings.from_env({"APP_ENV": "production", "LLM_PROVIDER": "openai-compatible"})
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        settings.validate_for_environment()


def test_given_malformed_cors_origins_then_validation_fails():
    settings = Settings.from_env({"APP_ENV": "production", "CORS_ORIGINS": "not-a-url"})
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        settings.validate_for_environment()
```

- [x] **Step 2: Run the focused settings tests and verify failure**

Run: `pytest tests/test_config.py -q`

Expected: FAIL because `config.py` and `Settings` do not exist.

- [x] **Step 3: Implement typed settings without adding an unverified dependency**

Parse the exact settings in the approved spec. Use standard-library parsing if it meets the existing dependency policy; otherwise add the smallest pinned settings dependency after checking the supported Python version. Validate production requirements, positive limits, allowed providers, explicit simulation behavior, CORS URL syntax, and repository-anchored paths.

- [x] **Step 4: Write failing application-factory tests**

```python
from fastapi.testclient import TestClient
from app_factory import create_app
from config import Settings


def test_given_development_settings_when_app_is_created_then_live_health_does_not_load_models():
    settings = Settings.from_env({"APP_ENV": "development", "SIMULATION_MODE": "true"})
    client = TestClient(create_app(settings))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "live"
```

- [x] **Step 5: Run the application-factory test and verify failure**

Run: `pytest tests/test_app_factory.py -q`

Expected: FAIL because `create_app` is not defined.

- [x] **Step 6: Refactor app construction**

Move global initialization behind `create_app`. Construct SQLite/vector/model adapters through dependencies. Preserve the existing local entrypoint behavior and make model loading lazy or injectable so tests and health routes do not require model downloads.

- [x] **Step 7: Update all path/model consumers**

Replace hardcoded paths, LM Studio URL, limits, CORS origins, and timeouts with `Settings`. Keep the engine allowlist; never construct an outbound model URL from a request body.

- [x] **Step 8: Run focused tests plus current suite**

Run: `pytest tests/test_config.py tests/test_app_factory.py -q`
Run: `pytest tests/ -q`

Expected: focused tests and the existing suite pass.

- [x] **Step 9: Commit**

```bash
git add config.py app_factory.py backend/app.py backend/rag_engine.py backend/agentic_router.py ingest_all.py tests/test_config.py tests/test_app_factory.py
 git commit -m "refactor: centralize foundation configuration and app lifecycle"
```

---

## Task 3: Add explicit tenant and principal context

**Files:**
- Create: `backend/tenant_context.py`, `tests/test_tenant_context.py`
- Modify: `backend/app.py`, `backend/rag_engine.py`, `backend/agentic_router.py`, `backend/contracts.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class PrincipalContext:
    subject: str
    tenant_id: str
    roles: frozenset[str]
    is_development_identity: bool = False


def get_principal_context(request: Request, settings: Settings) -> PrincipalContext: ...
def require_role(context: PrincipalContext, role: str) -> None: ...
def require_tenant_scope(context: PrincipalContext, tenant_id: str) -> None: ...
```

- [x] **Step 1: Write failing context tests**

```python
import pytest
from backend.tenant_context import PrincipalContext, require_role, require_tenant_scope


def test_given_principal_with_role_when_role_is_required_then_access_is_allowed():
    context = PrincipalContext("user-1", "tenant-a", frozenset({"adjuster"}))
    require_role(context, "adjuster")


def test_given_principal_from_tenant_a_when_tenant_b_is_requested_then_access_is_denied():
    context = PrincipalContext("user-1", "tenant-a", frozenset())
    with pytest.raises(PermissionError):
        require_tenant_scope(context, "tenant-b")
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_tenant_context.py -q`

Expected: FAIL because the context module does not exist.

- [x] **Step 3: Implement explicit context resolution**

Use a clearly marked development identity only in local mode. In production, reject missing or unverified identity rather than silently assigning the local tenant. Validate tenant identifiers and keep them server-derived. Add the context dependency to protected API routes.

- [x] **Step 4: Add tenant scope to storage calls**

Extend the storage contract in a backward-compatible way so production calls require `tenant_id`, while local adapters default only through the explicit development context. Update document listing, upload, delete, search, and claim retrieval to scope by tenant before claim scope.

- [x] **Step 5: Add isolation API tests**

Create two development principals and two isolated stores/fixtures. Prove tenant A cannot list, retrieve, download, or delete tenant B’s documents, even when filenames and claim IDs match.

- [x] **Step 6: Run focused and existing tests**

Run: `pytest tests/test_tenant_context.py tests/test_rag_engine.py tests/test_agentic_router.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add backend/tenant_context.py backend/app.py backend/rag_engine.py backend/agentic_router.py backend/contracts.py tests/test_tenant_context.py tests/test_rag_engine.py
 git commit -m "feat: establish explicit tenant and principal context"
```

---

## Task 4: Add blob-store and audit boundaries

**Files:**
- Create: `backend/blob_store.py`, `backend/audit.py`
- Create: `tests/test_blob_store.py`, `tests/test_audit.py`
- Modify: `backend/rag_engine.py`, `backend/app.py`, `.gitignore`

**Interfaces:**

```python
class DocumentBlobStore(ABC):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def create_download_url(self, key: str, expires_seconds: int) -> str: ...

class AuditSink(ABC):
    def record(self, event: dict[str, object]) -> None: ...
```

- [x] **Step 1: Write failing blob-store tests**

```python
import pytest
from backend.blob_store import LocalDocumentBlobStore


def test_given_tenant_scoped_key_when_written_then_content_can_be_read_back(tmp_path):
    store = LocalDocumentBlobStore(tmp_path)
    store.put("tenant-a/claim-1/report.txt", b"evidence", "text/plain")
    assert store.get("tenant-a/claim-1/report.txt") == b"evidence"


def test_given_traversal_key_when_written_then_operation_is_rejected(tmp_path):
    store = LocalDocumentBlobStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("tenant-a/../tenant-b/leak.txt", b"x", "text/plain")
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_blob_store.py -q`

Expected: FAIL because the interface/adapter does not exist.

- [x] **Step 3: Implement the interface and local adapter**

Use a path-confinement helper that rejects absolute paths, `..`, invalid identifiers, and symlink escapes. Write through a sibling temporary file and atomic replace. Do not expose filesystem paths as download URLs; return a local adapter token or route-controlled URL shape.

- [x] **Step 4: Write failing audit tests**

```python
from backend.audit import JsonlAuditSink


def test_given_audit_event_when_recorded_then_request_and_evidence_metadata_are_persisted(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.record({"event": "chat", "request_id": "req-1", "tenant_id": "tenant-a", "evidence_ids": ["ev-1"]})
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert '"request_id": "req-1"' in text
    assert '"evidence_ids": ["ev-1"]' in text
```

- [x] **Step 5: Implement append-only structured audit sink**

Validate required event fields, add UTC timestamp if absent, and use atomic/append-safe writes appropriate to the local adapter. Redact query/document content by default; record identifiers, outcome, model versions, and evidence IDs instead.

- [x] **Step 6: Integrate upload/delete/download audit events**

Record actor, tenant, claim, request ID, document ID, operation, outcome, and error code for upload, replacement, deletion, and download. Record chat request and returned evidence IDs without storing unnecessary raw claim content.

- [x] **Step 7: Run focused tests and current suite**

Run: `pytest tests/test_blob_store.py tests/test_audit.py tests/test_rag_engine.py -q`
Run: `pytest tests/ -q`

Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add backend/blob_store.py backend/audit.py backend/rag_engine.py backend/app.py tests/test_blob_store.py tests/test_audit.py .gitignore
 git commit -m "feat: add blob storage and audit boundaries"
```

---

## Task 5: Define the ICM Evidence/Interpretation/Decision response contract

**Files:**
- Create: `backend/contracts.py`, `tests/test_contracts.py`
- Modify: `backend/agentic_router.py`, `backend/app.py`, `eval/run_eval.py`, `README.md`

**Interfaces:**

```python
class Evidence(BaseModel): ...
class InterpretationClaim(BaseModel): ...
class Interpretation(BaseModel): ...
class DecisionBoundary(BaseModel): ...
class GroundedAnswer(BaseModel): ...
class PipelineMetadata(BaseModel): ...
class GroundedResponse(BaseModel): ...

def build_grounded_response(... ) -> GroundedResponse: ...
```

- [x] **Step 1: Write failing contract tests**

```python
import pytest
from pydantic import ValidationError
from backend.contracts import Evidence, GroundedResponse


def test_given_evidence_when_response_is_built_then_decision_defaults_to_not_a_decision():
    response = GroundedResponse.model_validate({
        "request_id": "req-1",
        "answer": {"text": "Supported", "status": "grounded", "interpretation": {"claims": []}},
        "evidence": [{"id": "ev-1", "document_id": "doc-1", "document_version": "sha256:x", "filename": "a.txt", "chunk_id": "chunk-1", "excerpt": "text", "retrieval": {"method": "hybrid", "rank": 1, "score": 0.9}}],
        "pipeline": {"embedding_model": "embed", "reranker_model": "rerank", "generation_model": "simulated", "retrieved_at": "2026-08-28T00:00:00Z"},
    })
    assert response.answer.decision_boundary.decision_status == "not_a_decision"


def test_given_evidence_reference_that_does_not_exist_when_response_is_validated_then_it_is_rejected():
    with pytest.raises(ValidationError, match="evidence"):
        GroundedResponse.model_validate({
            "request_id": "req-1",
            "answer": {"text": "Unsupported", "status": "grounded", "interpretation": {"claims": [{"text": "x", "evidence_ids": ["missing"]}]}},
            "evidence": [],
            "pipeline": {"embedding_model": "e", "reranker_model": "r", "generation_model": "g", "retrieved_at": "2026-08-28T00:00:00Z"},
        })
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_contracts.py -q`

Expected: FAIL because the structured contract does not exist.

- [x] **Step 3: Implement Pydantic models and cross-reference validation**

Implement the canonical response from the approved spec. Require stable evidence IDs, document/version identifiers, excerpt, retrieval metadata, interpretation status, explicit uncertainty, and decision boundary. Validate every interpretation evidence reference against returned evidence IDs.

- [x] **Step 4: Adapt router output**

Have the router create `GroundedResponse`. Preserve legacy `answer`, `sources`, `claim_dossier`, `engine`, and `pipeline_logs` as a compatibility projection generated from the structured response. Ensure the legacy source list is never assembled independently.

- [x] **Step 5: Add refusal and decision-boundary tests**

Test no-match refusal, claim-scoped evidence, unsupported claim detection, source conflict representation, calculations with operands/results, and default `not_a_decision` status.

- [x] **Step 6: Update evaluation output**

Make the evaluation harness score evidence and structured claims, while preserving existing result fields. Add a check that every cited source has a stable evidence ID and that no answer with zero evidence has status `grounded`.

- [x] **Step 7: Run focused and regression tests**

Run: `pytest tests/test_contracts.py tests/test_agentic_router.py -q`
Run: `pytest tests/ -q`
Run: `python eval/parity_runner.py`

Expected: PASS and parity remains at the existing baseline.

- [x] **Step 8: Commit**

```bash
git add backend/contracts.py backend/agentic_router.py backend/app.py eval/run_eval.py README.md tests/test_contracts.py
 git commit -m "feat: add interpretable grounded response contract"
```

---

## Task 6: Add health/readiness, request IDs, structured logs, and bounded API inputs

**Files:**
- Create: `backend/health.py`, `tests/test_health.py`, `tests/test_api_limits.py`
- Modify: `backend/app.py`, `config.py`, `backend/audit.py`, `README.md`

**Interfaces:**

```python
def live_status() -> dict[str, str]: ...
def ready_status(dependencies: DependencyChecks) -> tuple[dict, int]: ...
def validate_upload_metadata(...): ...
def validate_query_request(...): ...
```

- [x] **Step 1: Write failing health and limit tests**

```python
def test_given_running_app_when_liveness_is_requested_then_live_status_is_returned(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_given_query_over_maximum_length_when_submitted_then_request_is_rejected(client):
    response = client.post("/api/eval/search", json={"query": "x" * 100_000, "mode": "naive", "top_k": 4})
    assert response.status_code == 413
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_health.py tests/test_api_limits.py -q`

Expected: FAIL because the endpoints and limits are not implemented.

- [x] **Step 3: Implement liveness/readiness**

`/health/live` must not call external dependencies. `/health/ready` checks only dependencies required by the configured profile and returns a non-ready status with safe diagnostic codes when they are unavailable. Avoid loading ML models as a side effect of liveness.

- [x] **Step 4: Add request ID middleware and structured logging**

Accept a validated request ID or generate one. Include it in response headers, structured log records, audit events, and error responses. Do not log raw document content or full sensitive queries by default.

- [x] **Step 5: Enforce input limits**

Apply upload byte limits before parsing where possible, extracted-text limits after parsing, query length limits, bounded `top_k`, filename/type validation, and safe production error mapping. Ensure `/api/eval/search` is disabled or protected outside development/test.

- [x] **Step 6: Run focused, API, and security tests**

Run: `pytest tests/test_health.py tests/test_api_limits.py tests/test_api_security.py -q`
Run: `pytest tests/ -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add backend/health.py backend/app.py config.py backend/audit.py tests/test_health.py tests/test_api_limits.py README.md
 git commit -m "feat: add health checks request IDs and API limits"
```

---

## Task 7: Formalize ingestion jobs and idempotency-compatible upload responses

**Files:**
- Create: `backend/ingestion.py`, `tests/test_ingestion_contract.py`
- Modify: `backend/app.py`, `backend/rag_engine.py`, `ingest_all.py`, `backend/contracts.py`

**Interfaces:**

```python
class IngestionStatus(str, Enum):
    queued = "queued"
    parsing = "parsing"
    embedding = "embedding"
    indexed = "indexed"
    failed = "failed"
    deleted = "deleted"

class IngestionJob(BaseModel): ...
class IngestionService:
    def submit(..., idempotency_key: str | None = None) -> IngestionJob: ...
    def get(job_id: str, tenant_id: str) -> IngestionJob: ...
```

- [x] **Step 1: Write failing ingestion contract tests**

```python
def test_given_same_tenant_and_idempotency_key_when_upload_is_replayed_then_one_job_is_returned(service):
    first = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x", idempotency_key="k1")
    second = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x", idempotency_key="k1")
    assert first.job_id == second.job_id


def test_given_different_tenant_when_same_idempotency_key_is_used_then_jobs_are_distinct(service):
    first = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x", idempotency_key="k1")
    second = service.submit(tenant_id="tenant-b", filename="a.txt", content=b"x", idempotency_key="k1")
    assert first.job_id != second.job_id
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_ingestion_contract.py -q`

Expected: FAIL because the service does not exist.

- [x] **Step 3: Implement the status model and in-process service**

Use the approved state machine. The synchronous local implementation may transition to `indexed` in the request, but must retain job identity, idempotency key, safe failure status, and document checksum. Never mark a partially embedded document indexed.

- [x] **Step 4: Adapt upload responses**

Return a stable response containing `job_id`, status, document ID, and legacy timing/chunk fields. Add `GET /api/jobs/{job_id}` with tenant authorization. Preserve current synchronous local behavior behind configuration.

- [x] **Step 5: Add failure and retry tests**

Use a fake embedding engine that fails mid-document. Assert rollback, `failed` status, no searchable partial chunks, audit event, and safe client error. Replay the same successful and failed idempotency key.

- [x] **Step 6: Run focused and current tests**

Run: `pytest tests/test_ingestion_contract.py tests/test_rag_engine.py -q`
Run: `pytest tests/ -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add backend/ingestion.py backend/app.py backend/rag_engine.py ingest_all.py backend/contracts.py tests/test_ingestion_contract.py
 git commit -m "feat: formalize idempotent ingestion jobs"
```

---

## Task 8: Build the deterministic harness and prioritized gates

**Files:**
- Create: `backend/harness.py`, `tests/test_harness.py`, `scripts/run_foundation_gates.py`
- Create: `scripts/check_secrets.py`, `scripts/check_spec_ordering.py`, `scripts/check_docs.py`
- Modify: `README.md`, `CONTRIBUTING.md`, `.github/workflows/tests.yml`

**Interfaces:**

```python
@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    blocking: bool
    message: str
    evidence: tuple[str, ...]

class GateRunner:
    def run(self, root: Path, mode: str = "local") -> list[Finding]: ...

def run_gate(name: str, root: Path) -> int: ...
```

- [x] **Step 1: Write failing harness self-tests**

```python
def test_given_added_secret_pattern_when_secret_gate_runs_then_finding_is_blocking(tmp_path):
    (tmp_path / "bad.py").write_text("API_KEY = 'secret-value-that-is-long-enough'", encoding="utf-8")
    findings = run_gate("secrets", tmp_path)
    assert any(f.rule_id == "secret-scan" and f.blocking for f in findings)


def test_given_valid_spec_ordering_when_logic_and_test_change_together_then_no_ordering_finding(tmp_path):
    (tmp_path / "spec.md").write_text("# Spec", encoding="utf-8")
    (tmp_path / "logic.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "test_logic.py").write_text("def test_x(): pass", encoding="utf-8")
    assert not [f for f in run_gate("spec-ordering", tmp_path) if f.severity == "error"]
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_harness.py -q`

Expected: FAIL because the harness modules do not exist.

- [x] **Step 3: Implement finding model and gate runner**

Implement reproducible output with rule ID, severity, blocking state, message, and exact evidence. Support `--advisory` and `--gate` modes. Keep scanners deterministic and offline.

- [x] **Step 4: Implement P0 gates**

Add secret scanning, input/security pattern checks, isolation test invocation, evidence-grounding test invocation, migration checks when migrations exist, production-auth configuration checks, and dependency-audit integration. Critical/high security findings block according to the matrix.

- [x] **Step 5: Implement P1 and P2 sensors**

Add golden evaluation/parity invocation, static analysis invocation, spec-ordering, docs freshness, instruction/workflow tamper, diff-size, sensitive-infrastructure containment, and lesson-to-guardrail traceability. P2 sensors must report without silently failing the merge.

- [x] **Step 6: Add guardrail self-test and lesson traceability format**

Define a machine-readable guardrail registry in `backend/harness.py` or a small checked-in data file. Every blocking guardrail has known-bad and known-good tests. Every promoted guardrail references a lesson file and the lesson references its test.

- [x] **Step 7: Wire CI**

Add a Python foundation workflow that runs unit tests, the harness self-tests, secret scan, static/dependency checks, existing parity runner, and the golden suite according to the blocking/advisory policy. Keep the existing workflow intact until the new workflow is green.

- [x] **Step 8: Run the complete foundation gate**

Run: `python scripts/run_foundation_gates.py --mode gate`
Run: `pytest tests/ -q`
Run: `python eval/parity_runner.py`

Expected: all blocking gates pass; advisory findings are printed with evidence and do not mask failures.

- [x] **Step 9: Commit**

```bash
git add backend/harness.py scripts tests/test_harness.py README.md CONTRIBUTING.md .github/workflows/tests.yml
 git commit -m "ci: add deterministic foundation gates"
```

---

## Task 9: Documentation, release profile, and verification evidence

**Files:**
- Modify: `README.md`, `docs/enterprise-migration.md`, `.env.example`, `.gitignore`, `.github/workflows/tests.yml`
- Create: `docs/release-checklist.md`, `docs/operations/local-and-production.md`, `SECURITY.md`, `CONTRIBUTING.md` if absent
- Test: `tests/test_documentation_contract.py`

**Interfaces:**
- Public documentation must describe commands and configuration that actually exist.

- [x] **Step 1: Write documentation-contract tests**

```python
def test_given_public_installation_docs_when_checked_then_documented_commands_exist():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pytest tests/ -q" in readme
    assert "python scripts/run_foundation_gates.py" in readme
    assert "/health/live" in readme


def test_given_env_template_when_checked_then_each_required_foundation_setting_is_documented():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("APP_ENV", "RAG_DB_PATH", "MAX_UPLOAD_BYTES", "MAX_QUERY_CHARS", "SIMULATION_MODE"):
        assert key in env
```

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_documentation_contract.py -q`

Expected: FAIL for missing or incomplete documentation.

- [x] **Step 3: Write operator documentation**

Document local setup, production configuration, data locations, backups, health endpoints, logs, audit events, evidence/interpretation/decision semantics, model-provider safety, limitations, security reporting, and the phased roadmap. State that synthetic data is the only checked-in data.

- [x] **Step 4: Add release checklist**

Include actual commands for tests, parity, evaluation, secret scan, dependency audit, static security analysis, migration checks, documentation checks, and manual review of decision-boundary behavior. Require recording actual output.

- [x] **Step 5: Run documentation tests and complete suite**

Run: `pytest tests/test_documentation_contract.py -q`
Run: `pytest tests/ -q`
Run: `python scripts/run_foundation_gates.py --mode gate`

Expected: PASS with no undocumented required settings or commands.

- [x] **Step 6: Commit**

```bash
git add README.md docs .env.example .gitignore SECURITY.md CONTRIBUTING.md .github/workflows/tests.yml tests/test_documentation_contract.py
 git commit -m "docs: publish foundation release and operations guidance"
```

---

## Final verification checklist

- [x] Run `pytest tests/ -q` and retain actual output.
- [x] Run `python scripts/run_foundation_gates.py --mode gate` and retain actual output.
- [x] Run `python eval/parity_runner.py` and confirm parity baseline.
- [ ] Run the golden evaluation and confirm configured thresholds. *(manual — requires a live LM Studio judge)*
- [x] Run secret scanning against the full tree and changed files.
- [x] Run Python dependency audit and static security analysis.
- [x] Exercise `/health/live` with model services unavailable.
- [x] Exercise `/health/ready` with each configured dependency unavailable.
- [x] Prove tenant A cannot list/retrieve/download/delete tenant B data.
- [x] Prove claim A cannot overwrite or retrieve claim B documents.
- [x] Prove no-evidence requests refuse synthesis.
- [x] Prove interpretation citations reference returned evidence IDs.
- [x] Prove assistant responses default to `not_a_decision`.
- [x] Prove malformed uploads, traversal paths, private model URLs, giant queries, and oversized uploads are rejected safely.
- [x] Prove a failed embedding transaction leaves no searchable partial document.
- [x] Confirm all documented commands and environment variables match implementation.
- [ ] Review the full diff manually; do not self-merge. *(human step)*
