"""API registration. All mutable services belong to one application runtime."""
import os
import json
import tempfile
import threading
import time
import uuid
import weakref
import anyio
from dataclasses import replace
from fastapi import UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from backend.blob_store import PRESIGN_EXPIRES_SECONDS
from backend.rag_engine import EmbeddingEngine, safe_filename
from backend.document_limits import parse_bounded, DocumentLimitError, CONTENT_TYPES
from backend.agentic_router import CLAIMS_DATA
from backend.llm_client import LLMDeadlineExceeded, llm_deadline_scope
from backend.health import live_status, ready_status
from backend.authn import AuthenticationError
from backend.rbac import require_permission
from backend.rate_limit import RateLimitExceeded
from backend.ingestion import IngestionStatus
from config import REPO_ROOT


class _TimeBoxedStream(StreamingResponse):
    """SSE response with a hard wall-clock cap, so a client that keeps the
    connection open but stops reading (which blocks Starlette's ``send``
    forever) can't hold the chat concurrency slot and the upstream LLM
    response indefinitely. On timeout, closes the underlying sync generator
    directly so its ``finally`` runs and the slot is released. If a worker
    thread happens to be inside ``next()`` on that generator at the exact
    instant we try to close it, ``close()`` raises ValueError ("generator
    already executing"); in that case we leave it alone and let the thread's
    own completion, or ``weakref.finalize`` once the generator is collected,
    release the slot -- the caller's idempotent finish guard makes either
    path run exactly once."""

    def __init__(self, generator, max_seconds, *args, **kwargs):
        super().__init__(generator, *args, **kwargs)
        self._raw_generator = generator
        self._max_seconds = max_seconds

    async def __call__(self, scope, receive, send):
        # Close on every exit -- timeout, normal end, client disconnect or outer
        # cancellation -- not only on timeout: a cancelled send leaves the sync
        # generator suspended, so without this its finally (audit + slot release)
        # waits on garbage collection. Closing a finished generator is a no-op.
        try:
            with anyio.move_on_after(self._max_seconds):
                await super().__call__(scope, receive, send)
        finally:
            # The generator's finally writes the audit event (file lock + fsync);
            # run close() in a worker thread so that blocking I/O never runs on
            # the event loop. Shielded so an outer cancellation (e.g. the client
            # disconnecting) can't skip the close and leak the concurrency slot.
            with anyio.CancelScope(shield=True):
                try:
                    await anyio.to_thread.run_sync(self._raw_generator.close)
                except ValueError:
                    pass


class ChatRequest(BaseModel):
    query: str
    engine: str  # 'lm-studio' or 'simulated'
    claim_id: Optional[str] = None


class DeleteRequest(BaseModel):
    filename: str
    claim_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    claim_id: Optional[str] = None
    mode: str  # 'naive' (vector-only) | 'hybrid' (vector+FTS+RRF) | 'hybrid_rerank' (+ cross-encoder)
    top_k: int = 4

    def validate_limits(self, settings):
        if len(self.query) > settings.max_query_chars:
            raise ValueError("query exceeds configured maximum")
        if self.top_k > settings.max_top_k:
            raise ValueError("top_k exceeds configured maximum")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        return self


class _ConcurrencyGate:
    """Non-blocking in-flight-request counter, keyed by caller-chosen strings.
    Unlike SlidingWindowRateLimiter (requests per time window), this tracks how
    many requests are concurrently in progress right now, so a handful of slow
    chat/upload requests can't starve the thread pool for every other caller.
    The limit is passed in at acquire time (not fixed at construction) so tests
    can swap settings without rebuilding the gate."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def acquire(self, key: str, limit: int) -> bool:
        with self._lock:
            count = self._counts.get(key, 0)
            if count >= limit:
                return False
            self._counts[key] = count + 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            count = self._counts.get(key, 0)
            if count <= 1:
                self._counts.pop(key, None)
            else:
                self._counts[key] = count - 1


def register_routes(app, runtime):
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        # The server always mints its own id; a client-supplied one is kept
        # separately so a caller can't choose the id that becomes the audit
        # record's identity, while still being able to correlate its own logs.
        request.state.client_request_id = (
            supplied if supplied and len(supplied) <= 128 and supplied.isascii() and supplied.isprintable() else None
        )
        request_id = f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        if runtime.settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Non-blocking concurrency gates (Phase M4): distinct from _rate_limit_429,
    # which caps requests per minute, not how many are in flight at once. One
    # gate instance per app, long-lived across requests.
    _chat_gate = _ConcurrencyGate()
    _upload_gate = _ConcurrencyGate()
    _principal_gate = _ConcurrencyGate()

    def _acquire_slot(principal, global_gate: "_ConcurrencyGate", global_key: str, global_limit: int):
        principal_key = f"{principal.tenant_id}:{principal.subject}"
        if not _principal_gate.acquire(principal_key, runtime.settings.max_concurrent_per_principal):
            raise HTTPException(status_code=429, detail="Too many requests in progress.")
        if not global_gate.acquire(global_key, global_limit):
            _principal_gate.release(principal_key)
            raise HTTPException(status_code=503, detail="Service is at capacity.", headers={"Retry-After": "5"})
        return principal_key

    def _release_slot(principal_key: str, global_gate: "_ConcurrencyGate", global_key: str):
        _principal_gate.release(principal_key)
        global_gate.release(global_key)


    def _get_embedding_engine():
        with runtime.embedding_lock:
            if runtime.embedding_engine is None:
                runtime.embedding_engine = EmbeddingEngine(model_name=runtime.settings.embedding_model)
            return runtime.embedding_engine


    def get_current_tenant(request: Request):
        """FastAPI dependency resolving the authenticated principal (Phase 4.1).
        Every /api/* route declares this so no endpoint is reachable without a
        valid identity -- the configured provider chain (development / OIDC /
        service accounts) decides what "valid" means. Missing/invalid credentials
        map to 401; the resolved PrincipalContext is available to handlers that
        declare it as a parameter."""
        try:
            principal = runtime._authenticator.authenticate(request)
            if principal.is_development_identity and runtime.settings.app_env in {"development", "test"}:
                principal = replace(principal, tenant_id=runtime.settings.tenant_id)
            if principal.tenant_id != runtime.settings.tenant_id:
                raise HTTPException(status_code=403, detail="Tenant access denied.")
            return principal
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=401,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from None


    def require_permission_403(principal, permission: str) -> None:
        """Maps the RBAC PermissionError to a 403 for the request path."""
        try:
            require_permission(principal, permission)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None


    def require_claim_access_403(principal, claim_id: str) -> None:
        """Maps the claim-level ACL PermissionError to a 403 for the request path."""
        try:
            runtime._claim_access_policy.require_claim_access(principal, claim_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None


    def authorize_document(principal, filename, claim_id=None):
        """Looks up the document in the given scope. Claim access is checked
        BEFORE the lookup when claim_id is given, so an unauthorized caller
        gets 403 whether or not the file exists -- otherwise the lookup result
        (404 vs proceed) would itself be an existence oracle."""
        if claim_id is not None:
            require_permission_403(principal, "claims:read")
            require_claim_access_403(principal, claim_id)
        metadata = runtime.vector_store.get_document_metadata(filename, claim_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return metadata

    def project_result(request, result):
        from backend.contracts import structured_projection
        structured = structured_projection(
            request_id=request.state.request_id, legacy_result=result,
            embedding_model=runtime.settings.embedding_model, reranker_model=runtime.settings.reranker_model,
        ).model_dump(mode="json")
        sources = [{**source, "evidence_id": evidence["id"]}
                   for source, evidence in zip(result.get("sources") or [], structured["evidence"], strict=True)]
        return {**result, "sources": sources, "structured": structured}

    def guard_result(result, claim_id):
        """Post-generation answer guard (backend/answer_guard.py), applied before audit and
        projection so neither the caller nor the structured contract sees an unsupported
        claim outcome or off-allowlist contact as the assistant's answer."""
        from backend.agentic_router import claim_record_status
        from backend.answer_guard import apply_guard
        return apply_guard(
            result,
            mode=runtime.settings.answer_guard_mode,
            record_status=claim_record_status(claim_id),
            allowed_domains=runtime.settings.answer_guard_allowed_domains,
        )

    def conflict_result(result, query):
        """Evidence conflict check (backend/conflict_check.py). Runs only on a live, non-withheld
        answer with at least two distinct sources; any failure leaves the result unchanged, so
        the status simply stays as it was (this is a label, not a gate)."""
        if (runtime.settings.conflict_check == "off" or result.get("status") == "error"
                or (result.get("guard") or {}).get("action") == "withheld"
                or "simulated" in str(result.get("engine", ""))):
            return result
        from backend.conflict_check import assess_conflict
        assessment = assess_conflict(runtime._llm_client, query, result.get("sources") or [])
        return {**result, "conflict": assessment.to_dict()} if assessment is not None else result

    def provenance_result(result):
        """Answer provenance check (backend/provenance_check.py). Runs on a live, non-withheld
        answer: redacts answer lines that repeat an unretrieved filename or an identifier a
        source directed the assistant to reproduce, and withholds the answer when nothing
        substantive survives. Any failure leaves the answer unchanged."""
        mode = runtime.settings.provenance_check
        if (mode == "off" or result.get("status") == "error"
                or (result.get("guard") or {}).get("action") == "withheld"
                or "simulated" in str(result.get("engine", ""))):
            return result
        from backend.provenance_check import MIN_KEPT_CHARS, check_provenance, substantive_chars
        checked = check_provenance(runtime._llm_client, str(result.get("answer") or ""),
                                   result.get("sources") or [], mode=mode)
        if not (checked.redacted or checked.errors or checked.judged):
            return result
        report = {"action": "none", "findings": [f.to_dict() for f in checked.findings],
                  "judged": checked.judged, "errors": list(checked.errors)}
        if not checked.redacted:
            return {**result, "provenance": report}
        if substantive_chars(checked.answer) < MIN_KEPT_CHARS:
            from backend.provenance_check import WITHHELD_TEXT
            report["action"] = "withheld"
            return {**result, "provenance": report, "answer": WITHHELD_TEXT}
        report["action"] = "redacted"
        return {**result, "provenance": report, "answer": checked.answer}

    def tools_result(result):
        """Computed claim figures (the router's `tool_section`), applied last so the guard,
        provenance and conflict checks never see -- or redact -- the computed section. Live
        prose is checked against the trusted computed section + claim dossier and the
        untrusted retrieved sources (backend/figure_check.py); a withheld answer gets the
        section without a figure check. Never raises."""
        section = result.get("tool_section")
        if not section:
            return result
        answer = str(result.get("answer") or "")
        if "withheld" in {(result.get("guard") or {}).get("action"),
                          (result.get("provenance") or {}).get("action")}:
            return {**result, "answer": answer + "\n\n---\n\n" + section}
        try:
            from backend.figure_check import check_figures, notice
            sources = [s.get("content") or "" for s in result.get("sources") or []]
            check = check_figures(answer, trusted=[section, result.get("claim_dossier") or ""],
                                  sources=sources)
            flagged = notice(check)
            if flagged:
                answer += "\n\n" + flagged
            return {**result, "answer": answer + "\n\n---\n\n" + section, "figure_check": check.to_dict()}
        except Exception as exc:  # noqa: BLE001 -- a check failure must not lose the computed section
            import logging
            logging.getLogger(__name__).warning("figure check failed: %s", type(exc).__name__)
            return {**result, "answer": str(result.get("answer") or "") + "\n\n---\n\n" + section}

    def guard_audit_fields(result):
        """Metadata-only guard summary for the audit sink: action and finding categories,
        never the excerpts, which are answer text."""
        fields = {}
        if result.get("conflict") is not None:
            fields["conflict_disagreement"] = result["conflict"]["disagreement"]
        guard = result.get("guard")
        if guard:
            fields.update({"guard_action": guard["action"],
                           "guard_findings": sorted({f"{f['kind']}:{f['category']}" for f in guard["findings"]})})
        provenance = result.get("provenance")
        if provenance and provenance["action"] != "none":
            fields.update({"provenance_action": provenance["action"],
                           "provenance_findings": sorted({f["kind"] for f in provenance["findings"]})})
        return fields

    def validate_chat(req):
        if not req.query.strip():
            raise HTTPException(status_code=400, detail="Query must not be empty.")
        if len(req.query) > runtime.settings.max_query_chars:
            raise HTTPException(status_code=413, detail="Query exceeds configured maximum.")
        if req.engine not in {"simulated", "lm-studio"}:
            raise HTTPException(status_code=400, detail="Unsupported engine.")
        if req.engine == "simulated" and not runtime.settings.simulation_mode:
            raise HTTPException(status_code=403, detail="Simulation is disabled.")

    def _rate_limit_429(principal) -> None:
        """Per-principal request limit on sensitive endpoints (Phase 4.4).
        Keyed by tenant + subject so one adjuster exhausting the allowance cannot
        starve another in the same tenant. Maps RateLimitExceeded to a 429."""
        try:
            runtime._rate_limiter.check(f"{principal.tenant_id}:{principal.subject}")
        except RateLimitExceeded:
            raise HTTPException(
                status_code=429,
                detail="Too many requests -- please slow down.",
                headers={"Retry-After": str(int(runtime._rate_limiter.window_seconds))},
            ) from None


    def _audit(request: Request, principal, event: str, **fields) -> None:
        """Records a metadata audit event through the configured sink. Every
        authenticated action carries who (subject), tenant, request_id, and a UTC
        timestamp (added by the sink), plus action-specific fields (filename,
        claim_id, sources, outcome); full question/answer text is omitted."""
        fields.pop("query", None)
        fields.pop("answer", None)
        runtime._audit_sink.record(
            {
                "event": event,
                "request_id": request.state.request_id,
                "client_request_id": getattr(request.state, "client_request_id", None),
                "tenant_id": principal.tenant_id,
                "subject": principal.subject,
                **fields,
            }
        )


    @app.get("/health/live")
    def health_live():
        return live_status()


    @app.get("/health/ready")
    def health_ready():
        payload, status_code = ready_status({"database": lambda: runtime.vector_store._connect().close()})
        return JSONResponse(content=payload, status_code=status_code)


    @app.get("/api/auth/me", dependencies=[Depends(get_current_tenant)])
    def auth_me(principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Returns the authenticated principal -- subject, tenant, roles. The
        frontend calls this on load to decide whether to show the login gate and
        to display who is signed in."""
        return {
            "subject": principal.subject,
            "tenant_id": principal.tenant_id,
            "roles": sorted(principal.roles),
            "is_development_identity": principal.is_development_identity,
        }


    @app.get("/api/status", dependencies=[Depends(get_current_tenant)])
    def get_status(principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Checks the status of the configured LLM gateway (Phase 5.1).
        Model discovery now runs through the injected ChatClient (an
        OpenAICompatibleClient with a TTL'd model cache) rather than an inline
        HTTP round-trip, so this endpoint and /api/chat agree on the served
        models. With LLM_PROVIDER=none (simulation only) there is nothing to
        probe and the Lemonade block reports inactive."""
        require_permission_403(principal, "documents:read")
        models = runtime._llm_client.models() if runtime._llm_client is not None else []
        lm_studio_models = models or []
        lm_studio_active = bool(models and models != ["local-model"] and any("embed" not in name.lower() for name in models))

        return {
            "lm_studio": {
                "active": lm_studio_active,
                "url": None,
                "models": lm_studio_models
            },
            "database": {
                "backend": runtime.settings.vector_store,
                "document_count": len(runtime.vector_store.get_all_documents())
            },
            "simulation": {"enabled": runtime.settings.simulation_mode},
            "ingestion": {
                "mode": runtime.settings.ingestion_mode
            }
        }


    @app.get("/api/documents", dependencies=[Depends(get_current_tenant)])
    def list_documents(principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Lists all processed documents."""
        require_permission_403(principal, "documents:read")
        return runtime.vector_store.get_all_documents()


    @app.get("/api/claims", dependencies=[Depends(get_current_tenant)])
    def list_claims(principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Serves the claims queue -- the single source of truth CLAIMS_DATA
        (backend/agentic_router.py) the agentic router already grounds claim-scoped
        answers in, so the frontend's claim cards can't drift out of sync with it.
        Phase 4.2: filtered by the claim-level ACL -- adjusters see only claims
        assigned to them; admin/supervisor/siu see all."""
        require_permission_403(principal, "claims:read")
        return runtime._claim_access_policy.filter_claims(principal, CLAIMS_DATA)


    @app.delete("/api/claims/{claim_id}", dependencies=[Depends(get_current_tenant)])
    def purge_claim_route(request: Request, claim_id: str, principal=Depends(get_current_tenant)):  # noqa: B008
        """Purges a whole claim: deletes its indexed documents and ingestion
        job rows (M12c-E, enterprise audit finding #6). Admin-only
        (claims:purge); refuses with 409 while any ingestion job for the
        claim is still in flight, so a purge can never race an upload/delete
        that's mid-flight."""
        from backend.claim_purge import ClaimPurgeConflict, purge_claim

        try:
            require_permission_403(principal, "claims:purge")
        except HTTPException:
            _audit(request, principal, "claim_purge", outcome="denied", claim_id=claim_id)
            raise
        _rate_limit_429(principal)
        try:
            result = purge_claim(
                vector_store=runtime.vector_store,
                job_store=runtime.ingestion_job_store,
                tenant_id=principal.tenant_id,
                claim_id=claim_id,
            )
        except ValueError:
            _audit(request, principal, "claim_purge", outcome="invalid", claim_id=claim_id)
            raise HTTPException(status_code=400, detail="Invalid claim id.") from None
        except ClaimPurgeConflict:
            _audit(request, principal, "claim_purge", outcome="conflict", claim_id=claim_id)
            raise HTTPException(status_code=409, detail="Claim has ingestion jobs in progress.") from None
        _audit(request, principal, "claim_purge", outcome="purged", claim_id=claim_id,
               document_count=result.documents_deleted, job_count=result.jobs_deleted)
        return {
            "claim_id": claim_id,
            "documents_deleted": result.documents_deleted,
            "jobs_deleted": result.jobs_deleted,
        }


    @app.get("/api/jobs/{job_id}", dependencies=[Depends(get_current_tenant)])
    def get_ingestion_job(job_id: str, principal=Depends(get_current_tenant)):  # noqa: B008
        """Returns the durable ingestion job record for an upload (Phase 3.2).
        The frontend polls this while an async upload runs, so the UI reflects
        real pipeline state instead of a fake progress bar. Tenant-guarded: a job
        whose tenant does not match the configured tenant is not readable."""
        try:
            job = runtime.ingestion_service.get(job_id, principal.tenant_id)
            require_permission_403(principal, "documents:read")
            if job.claim_id:
                require_claim_access_403(principal, job.claim_id)
            return job
        except KeyError:
            raise HTTPException(status_code=404, detail="Job not found.") from None
        except PermissionError:
            raise HTTPException(status_code=403, detail="Forbidden.") from None


    @app.post("/api/upload")
    def upload_document(request: Request, file: UploadFile = File(...), principal=Depends(get_current_tenant)):  # noqa: B008
        require_permission_403(principal, "documents:upload")
        principal_key = _acquire_slot(principal, _upload_gate, "upload", runtime.settings.max_concurrent_uploads)
        try:
            return process_upload(request, file, principal)
        finally:
            _release_slot(principal_key, _upload_gate, "upload")

    @app.post("/api/upload-claim-file")
    def upload_claim_document(request: Request, claim_id: str = Form(...), file: UploadFile = File(...), principal=Depends(get_current_tenant)):  # noqa: B008
        require_permission_403(principal, "claims:write")
        require_claim_access_403(principal, claim_id)
        principal_key = _acquire_slot(principal, _upload_gate, "upload", runtime.settings.max_concurrent_uploads)
        try:
            return process_upload(request, file, principal, claim_id)
        finally:
            _release_slot(principal_key, _upload_gate, "upload")

    def process_upload(request, file, principal, claim_id=None):

        """Uploads and processes a claim reference document.

        Sync mode (default): parses and indexes inline, returning the timing
        payload plus job_id/status so the response contract is compatible with the
        async shape (Phase 3.2). Async mode (INGESTION_MODE=async): validates
        cheaply (filename/extension/empty), stages the bytes in the blob store,
        returns 202 + job_id, and the ingestion worker parses/embeds/indexes off
        the request thread -- the frontend polls GET /api/jobs/{job_id} for real
        pipeline state.
        """
        _rate_limit_429(principal)
        if not file.filename:
            _audit(request, principal, "upload", outcome="failed", filename=None, reason="missing filename")
            raise HTTPException(status_code=400, detail="Missing filename.")
        try:
            filename = safe_filename(file.filename)
        except ValueError:
            _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason="invalid filename")
            raise HTTPException(status_code=400, detail="Invalid filename.") from None
        file_ext = filename.split(".")[-1].lower()
        if file_ext not in ["pdf", "docx", "xlsx", "txt"]:
            _audit(request, principal, "upload", outcome="failed", filename=filename, reason="unsupported format")
            raise HTTPException(
                status_code=400,
                detail="Unsupported file format. Please upload PDF, DOCX, Excel, or Text documents."
            )

        content = file.file.read(runtime.settings.max_upload_bytes + 1)
        if len(content) > runtime.settings.max_upload_bytes:
            _audit(request, principal, "upload", outcome="failed", filename=filename, reason="upload exceeds size limit")
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the maximum upload size of {runtime.settings.max_upload_bytes // (1024 * 1024)} MB.",
            )

        if runtime.settings.ingestion_mode == "async":
            if not content:
                _audit(request, principal, "upload", outcome="failed", filename=filename, reason="empty document")
                raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")
            response = _enqueue_upload(content, filename, file_ext, claim_id=claim_id)
            _audit(request, principal, "upload", outcome="queued", filename=filename, claim_id=claim_id,
                   job_id=json.loads(response.body)["job_id"])
            return response

        start_time = time.time()

        # Create temp file to read from
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # Step 1: Text extraction
            parse_start = time.time()
            try:
                text = parse_bounded(tmp_path, file_ext, runtime.settings.max_upload_bytes, runtime.settings.max_document_chars, runtime.settings.request_timeout_seconds,
                                      ocr_fallback_mode=runtime.settings.ocr_fallback_mode, ocr_fallback_python=runtime.settings.ocr_fallback_python, ocr_fallback_timeout=runtime.settings.ocr_fallback_timeout_seconds)
            except DocumentLimitError as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from None
            except Exception:
                # A corrupt/truncated/password-protected file raises deep inside
                # pypdf/python-docx/pandas. That's a client error, not a server
                # failure -- a 500 with the internal exception text (what the
                # generic handler would return) leaks internals and misleads the
                # user into retrying an unreadable file.
                raise HTTPException(
                    status_code=400,
                    detail="Could not read the uploaded file -- it may be corrupt, truncated, or password-protected."
                ) from None
            parse_time = (time.time() - parse_start) * 1000

            if not text.strip():
                raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")

            # Step 2: Save to vector database (which chunks & embeds hierarchically)
            db_start = time.time()
            file_size = os.path.getsize(tmp_path)
            doc_id, parent_count = runtime.vector_store.add_document(
                filename=filename,
                file_type=file_ext,
                file_size=file_size,
                text=text,
                embedding_engine=runtime._get_embedding_engine(),
                claim_id=claim_id,
            file_path=tmp_path
            )
            db_time = (time.time() - db_start) * 1000

            total_time = (time.time() - start_time) * 1000

            # Record the finished job so the response carries a job_id (the async
            # contract), keeping sync and async response shapes compatible. The
            # document is already indexed and searchable at this point, so a
            # job-store failure here (e.g. a locked jobs.db) must not turn a
            # committed upload into a 500.
            job = None
            try:
                job = runtime.ingestion_service.record_result(
                    tenant_id=runtime.settings.tenant_id,
                    filename=filename,
                    content=content,
                    document_id=str(doc_id),
                    claim_id=claim_id,
                )
            except Exception as exc:  # noqa: BLE001 -- the upload is already committed
                import logging
                logging.getLogger(__name__).error("job record_result failed after upload: %s", type(exc).__name__)

            _audit(request, principal, "upload", outcome="indexed", filename=filename, claim_id=claim_id,
                   file_size=file_size, chunks_count=parent_count, job_id=job.job_id if job else None,
                   job_record="ok" if job else "failed")

            return {
                "filename": filename,
            "claim_id": claim_id,
                "chunks_count": parent_count,
                "total_time_ms": round(total_time, 1),
                "job_id": job.job_id if job else None,
                "status": job.status.value if job else IngestionStatus.indexed.value,
                "steps": {
                    "parsing_ms": round(parse_time, 1),
                    "db_storage_ms": round(db_time, 1)
                }
            }
        except HTTPException as exc:
            # Deliberate 4xx responses (empty document, corrupt file) must not
            # be re-wrapped by the generic handler into a 500.
            _audit(request, principal, "upload", outcome="failed", filename=filename, reason=str(exc.detail))
            raise
        except ValueError as e:
            # safe_filename() rejections are client errors, not server failures.
            _audit(request, principal, "upload", outcome="failed", filename=filename, reason=str(e))
            raise HTTPException(status_code=400, detail="Document processing failed.") from None
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("upload failed: %s", type(exc).__name__)
            _audit(request, principal, "upload", outcome="failed", filename=filename, reason="internal error")
            raise HTTPException(status_code=500, detail="Document processing failed.") from None
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


    @app.get("/api/documents/claim/{claim_id}", dependencies=[Depends(get_current_tenant)])
    def list_claim_documents(request: Request, claim_id: str, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Lists all documents attached to a specific claim."""
        require_permission_403(principal, "claims:read")
        try:
            require_claim_access_403(principal, claim_id)
        except HTTPException:
            _audit(request, principal, "claim_documents", outcome="denied", claim_id=claim_id)
            raise
        documents = runtime.vector_store.get_claim_documents(claim_id)
        _audit(request, principal, "claim_documents", outcome="listed", claim_id=claim_id,
               document_count=len(documents))
        return documents


    @app.get("/api/documents/content/{filename}", dependencies=[Depends(get_current_tenant)])
    def get_document_content(request: Request, filename: str, version: str | None = None, claim_id: str | None = None, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Fetches the full text content of a document by joining all its parent chunks."""
        require_permission_403(principal, "documents:read")
        _rate_limit_429(principal)
        try:
            filename = safe_filename(filename)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid filename.") from None
        # Backend-neutral: the store joins its own parent chunks. (This previously
        # opened an inline sqlite connection on vector_store.db_path, which could
        # not work on a Postgres backend.)
        try:
            metadata = authorize_document(principal, filename, claim_id)
        except HTTPException as exc:
            _audit(request, principal, "content", outcome="not_found" if exc.status_code == 404 else "denied",
                   filename=filename, claim_id=claim_id)
            raise
        if version is not None and version != metadata["document_version"]:
            raise HTTPException(status_code=409, detail="This source has changed. Run the query again.")
        full_text = runtime.vector_store.get_document_content(filename, claim_id)
        if not full_text:
            _audit(request, principal, "content", outcome="not_found", filename=filename, claim_id=claim_id)
            raise HTTPException(status_code=404, detail="Document content not found.")
        _audit(request, principal, "content", outcome="served", filename=filename, claim_id=claim_id)
        return {"filename": filename, "content": full_text}


    @app.get("/api/documents/download/{filename}", dependencies=[Depends(get_current_tenant)])
    def download_document(request: Request, filename: str, version: str | None = None, claim_id: str | None = None, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Serves the physical document. With an object store configured this is a
        redirect to a presigned URL; otherwise the file streams from the local
        storage directory. Both paths keep the source bytes behind the store's
        serving abstraction rather than exposing a raw filesystem path."""
        require_permission_403(principal, "documents:read")
        try:
            filename = safe_filename(filename)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid filename.") from None
        try:
            metadata = authorize_document(principal, filename, claim_id)
        except HTTPException as exc:
            _audit(request, principal, "download", outcome="not_found" if exc.status_code == 404 else "denied", filename=filename, claim_id=claim_id)
            raise
        if version is not None and version != metadata["document_version"]:
            raise HTTPException(status_code=409, detail="This source has changed. Run the query again to view current evidence.")
        blob_store = getattr(runtime.vector_store, "blob_store", None)
        if blob_store is not None:
            # The scope (claim vs global) is encoded in the blob key; the store
            # resolves it by looking the document up in the DB.
            from backend.rag_engine import _blob_key
            key = metadata.get("storage_key") or _blob_key(metadata.get("claim_id"), filename)
            if key is None:
                _audit(request, principal, "download", outcome="not_found", filename=filename, claim_id=claim_id)
                raise HTTPException(status_code=404, detail="File not found.")
            url = blob_store.create_download_url(key, PRESIGN_EXPIRES_SECONDS, filename=filename)
            _audit(request, principal, "download", outcome="redirected", filename=filename, claim_id=claim_id)
            return RedirectResponse(url)
        from backend.blob_store import LocalDocumentBlobStore
        file_path = LocalDocumentBlobStore(runtime.vector_store.storage_dir)._path(metadata.get("storage_key") or filename)
        if not os.path.exists(file_path):
            _audit(request, principal, "download", outcome="not_found", filename=filename, claim_id=claim_id)
            raise HTTPException(status_code=404, detail="File not found.")
        _audit(request, principal, "download", outcome="served", filename=filename, claim_id=claim_id)
        return FileResponse(file_path, filename=filename)


    @app.post("/api/delete", dependencies=[Depends(get_current_tenant)])
    def delete_document(request: Request, req: DeleteRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Deletes a document from the store.

        Sync mode (default): deletes inline (404 when absent) and records a
        'deleted' job so the response carries a job_id. Async mode: creates a
        queued delete job and enqueues a delete message, returning 202 + job_id;
        the worker performs the deletion and advances the job to 'deleted'."""
        require_permission_403(principal, "documents:delete")
        _rate_limit_429(principal)
        try:
            filename = safe_filename(req.filename)
        except ValueError:
            _audit(request, principal, "delete", outcome="failed", filename=req.filename, reason="invalid filename")
            raise HTTPException(status_code=400, detail="Invalid filename.") from None

        try:
            authorize_document(principal, filename, req.claim_id)
        except HTTPException as exc:
            _audit(request, principal, "delete", outcome="not_found" if exc.status_code == 404 else "denied", filename=filename, claim_id=req.claim_id)
            raise

        if runtime.settings.ingestion_mode == "async":
            job = runtime.ingestion_service.submit_delete(tenant_id=runtime.settings.tenant_id, filename=filename, claim_id=req.claim_id)
            _audit(request, principal, "delete", outcome="queued", filename=filename, claim_id=req.claim_id, job_id=job.job_id)
            return JSONResponse(
                status_code=202,
                content={
                    "job_id": job.job_id,
                    "status": job.status.value,
                    "filename": filename,
                },
            )

        deleted = runtime.vector_store.delete_document(filename, req.claim_id)
        if not deleted:
            _audit(request, principal, "delete", outcome="not_found", filename=filename, claim_id=req.claim_id)
            raise HTTPException(status_code=404, detail="Document not found.")
        # The document is already deleted from the vector store; a job-store
        # failure here must not turn the committed delete into a 500.
        job = None
        try:
            job = runtime.ingestion_service.record_result(
                tenant_id=runtime.settings.tenant_id,
                filename=filename,
                content=b"",
                claim_id=req.claim_id,
                status=IngestionStatus.deleted,
            )
        except Exception as exc:  # noqa: BLE001 -- the delete is already committed
            import logging
            logging.getLogger(__name__).error("job record_result failed after delete: %s", type(exc).__name__)

        # The document is already gone; a scrub failure must not turn the
        # committed delete into a 500 (the worker path swallows it the same way).
        # Scrub even when record_result failed: the upload's own job row still
        # names the file.
        try:
            runtime.ingestion_service.scrub_document(
                tenant_id=runtime.settings.tenant_id, filename=filename, claim_id=req.claim_id
            )
        except Exception as exc:  # noqa: BLE001 -- the delete is already committed
            import logging
            logging.getLogger(__name__).warning("job scrub failed after delete: %s", type(exc).__name__)
        _audit(request, principal, "delete", outcome="deleted", filename=filename, claim_id=req.claim_id,
               job_id=job.job_id if job else None, job_record="ok" if job else "failed")
        return {
            "message": f"Successfully deleted '{filename}'.",
            "job_id": job.job_id if job else None,
            "status": job.status.value if job else IngestionStatus.deleted.value,
        }


    @app.post("/api/chat", dependencies=[Depends(get_current_tenant)])
    def chat_with_docs(request: Request, req: ChatRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Answers a claims question using local Agentic RAG routing."""
        require_permission_403(principal, "documents:read")
        _rate_limit_429(principal)
        validate_chat(req)
        if req.claim_id:
            require_permission_403(principal, "claims:read")
            try:
                require_claim_access_403(principal, req.claim_id)
            except HTTPException:
                _audit(request, principal, "chat", outcome="denied", claim_id=req.claim_id)
                raise
        principal_key = _acquire_slot(principal, _chat_gate, "chat", runtime.settings.max_concurrent_chat)
        try:
            with llm_deadline_scope(runtime.settings.chat_deadline_seconds):
                result = runtime.agentic_router.run_query(
                    query_text=req.query,
                    claim_id=req.claim_id,
                    engine=req.engine,
                    embedding_engine=runtime._get_embedding_engine(),
                    vector_store=runtime.vector_store,
                    reranking_engine=runtime._reranker,
                    llm_client=runtime._llm_client,
                    caps=runtime.settings,
                )
                result = tools_result(conflict_result(provenance_result(guard_result(result, req.claim_id)), req.query))
            _audit(
                request,
                principal,
                "chat",
                query=req.query,
                claim_id=req.claim_id,
                engine=req.engine,
                answer=result.get("answer"),
                sources=[source.get("filename") for source in result.get("sources", [])],
                **guard_audit_fields(result),
            )
            return project_result(request, result)
        except LLMDeadlineExceeded:
            _audit(request, principal, "chat", outcome="timeout", query=req.query, claim_id=req.claim_id, engine=req.engine)
            raise HTTPException(status_code=504, detail="The answer took too long; try again.") from None
        finally:
            _release_slot(principal_key, _chat_gate, "chat")


    @app.post("/api/chat/stream", dependencies=[Depends(get_current_tenant)])
    def chat_stream(request: Request, req: ChatRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """SSE counterpart to /api/chat: same auth/RBAC/rate-limit/concurrency checks
        run synchronously before the generator starts (so a 401/403/429/503 is a
        normal HTTP status, not something swallowed once the stream has begun).
        The concurrency slot is held for the generator's whole life and released in
        its `finally`, and the audit record is written there too -- exactly once,
        whether the stream reaches `final` or the client disconnects first."""
        require_permission_403(principal, "documents:read")
        _rate_limit_429(principal)
        validate_chat(req)
        if req.claim_id:
            require_permission_403(principal, "claims:read")
            try:
                require_claim_access_403(principal, req.claim_id)
            except HTTPException:
                _audit(request, principal, "chat", outcome="denied", claim_id=req.claim_id)
                raise
        principal_key = _acquire_slot(principal, _chat_gate, "chat", runtime.settings.max_concurrent_chat)
        finished = threading.Event()

        def _finish(final_event):
            # Idempotent: called from gen()'s finally and, via weakref.finalize,
            # when a generator that never started (disconnect before the first
            # next()) is collected -- its finally would never run, leaking the slot.
            if finished.is_set():
                return
            finished.set()
            try:
                _audit(request, principal, "chat",
                       query=req.query, claim_id=req.claim_id,
                       engine=(final_event or {}).get("engine", req.engine),
                       answer=(final_event or {}).get("answer"),
                       sources=[s.get("filename") for s in ((final_event or {}).get("sources") or [])],
                       status=(final_event or {}).get("status"),
                       outcome="completed" if final_event is not None else "aborted",
                       **guard_audit_fields(final_event or {}))
            finally:
                _release_slot(principal_key, _chat_gate, "chat")

        def gen():
            final_event = None
            try:
                for event in runtime.agentic_router.run_query_stream(
                        query_text=req.query, claim_id=req.claim_id, engine=req.engine,
                        embedding_engine=runtime._get_embedding_engine(), vector_store=runtime.vector_store,
                        reranking_engine=runtime._reranker, llm_client=runtime._llm_client, caps=runtime.settings):
                    if event["type"] == "chunk":
                        yield f"data: {json.dumps({'text': event['text']})}\n\n"
                    elif event["type"] == "final":
                        # Chunks already streamed cannot be recalled; the final event's answer
                        # replaces the streamed bubble (frontend finalizeStreamingBubble), so a
                        # withheld answer is withdrawn from view once generation completes.
                        final_event = project_result(request, tools_result(conflict_result(
                            provenance_result(guard_result(event, req.claim_id)), req.query)))
                        yield f"data: {json.dumps({k: v for k, v in final_event.items() if k != 'type'})}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                # Runs exactly once, whether the loop above finished normally,
                # raised, or was torn down early by GeneratorExit (client
                # disconnect) -- so every stream leaves exactly one audit record.
                _finish(final_event)

        stream = gen()
        # weakref.finalize's callback can run synchronously on whatever thread
        # drops the last reference to `stream` -- including the event loop
        # thread's own refcounting -- so it must not run _finish's blocking
        # audit write (file lock + fsync) inline. Hand it to a daemon thread;
        # the explicit close path above stays responsible for the common case.
        weakref.finalize(
            stream, lambda: threading.Thread(target=_finish, args=(None,), daemon=True).start()
        )
        # MAX_STREAM_SECONDS bounds the whole SSE response, not a single call the
        # way request_timeout_seconds does -- default 300s (2.5x the default
        # request_timeout_seconds) leaves room for slow local-LLM token generation
        # while still capping a stalled-reader slot leak to a few minutes.
        return _TimeBoxedStream(stream, runtime.settings.max_stream_seconds, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


    def _enqueue_upload(content: bytes, filename: str, file_ext: str, claim_id: str | None) -> JSONResponse:
        """Async-mode upload: stage the source bytes in the blob store, enqueue an
        ingestion job, and return the 202 response the frontend polls against."""
        filename = safe_filename(filename)
        staging_key = f"staging/{uuid.uuid4().hex}/{filename}"
        runtime._async_blob_store.put(staging_key, content, _content_type(file_ext))
        job = runtime.ingestion_service.submit(
            tenant_id=runtime.settings.tenant_id,
            filename=filename,
            content=content,
            claim_id=claim_id,
            blob_key=staging_key,
        )
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.job_id,
                "status": job.status.value,
                "filename": filename,
                "claim_id": claim_id,
            },
        )


    def _content_type(file_ext: str) -> str:
        return CONTENT_TYPES.get(file_ext, "application/octet-stream")


    @app.post("/api/eval/search", dependencies=[Depends(get_current_tenant)])
    def eval_search(request: Request, req: SearchRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Raw retrieval endpoint (no LLM synthesis) for the eval harness to compare
        retrieval strategies. Not used by the frontend. Gated to roles with the
        eval:search permission (siu/admin) per Phase 4.2/4.4."""
        require_permission_403(principal, "eval:search")
        _rate_limit_429(principal)
        try:
            req.validate_limits(runtime.settings)
        except ValueError as exc:
            # Oversized query/top_k is a bounded-input violation -- answer the
            # abuse drill with a 413 (the same status as an oversized upload).
            raise HTTPException(status_code=413, detail=str(exc)) from None
        if req.mode not in ("naive", "hybrid", "hybrid_rerank"):
            raise HTTPException(status_code=400, detail="mode must be 'naive', 'hybrid', or 'hybrid_rerank'")

        if req.claim_id:
            try:
                require_claim_access_403(principal, req.claim_id)
            except HTTPException:
                _audit(request, principal, "eval_search", outcome="denied", claim_id=req.claim_id, mode=req.mode)
                raise
        query_emb = runtime._get_embedding_engine().embed_query(req.query)
        matches = runtime.vector_store.search_similarity(
            query_emb,
            req.query,
            claim_id=req.claim_id,
            reranking_engine=runtime._reranker if req.mode == "hybrid_rerank" else None,
            top_k=req.top_k,
            candidate_pool=runtime.settings.rerank_candidate_pool,
            use_fts=(req.mode != "naive"),
        )
        _audit(request, principal, "eval_search", outcome="completed", claim_id=req.claim_id, mode=req.mode,
               result_count=len(matches))
        return {"mode": req.mode, "matches": matches}

    runtime._get_embedding_engine = _get_embedding_engine
    app.mount("/", StaticFiles(directory=str(REPO_ROOT / "frontend"), html=True), name="frontend")
