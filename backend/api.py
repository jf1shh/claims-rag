"""API registration. All mutable services belong to one application runtime."""
import os
import json
import tempfile
import time
import uuid
from dataclasses import replace
from fastapi import UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from backend.rag_engine import EmbeddingEngine, safe_filename
from backend.document_limits import parse_bounded, DocumentLimitError
from backend.agentic_router import CLAIMS_DATA
from backend.health import live_status, ready_status
from backend.authn import AuthenticationError
from backend.rbac import require_permission
from backend.rate_limit import RateLimitExceeded
from backend.ingestion import IngestionStatus
from config import REPO_ROOT


class ChatRequest(BaseModel):
    query: str
    engine: str  # 'lm-studio' or 'simulated'
    claim_id: Optional[str] = None


class DeleteRequest(BaseModel):
    filename: str


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


_CONTENT_TYPES = {
    "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xls": "application/vnd.ms-excel", "txt": "text/plain",
}


def register_routes(app, runtime):
    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        import logging
        logging.getLogger(__name__).error("request failed: %s", type(exc).__name__)
        return JSONResponse({"detail": "An internal error occurred."}, status_code=500)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if supplied and len(supplied) <= 128 and supplied.isascii() and supplied.isprintable() else f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


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


    def authorize_document(principal, filename):
        metadata = runtime.vector_store.get_document_metadata(filename)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        if metadata.get("claim_id"):
            require_permission_403(principal, "claims:read")
            require_claim_access_403(principal, metadata["claim_id"])
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
    def get_status():
        """Checks the status of the configured LLM gateway (Phase 5.1).
        Model discovery now runs through the injected ChatClient (an
        OpenAICompatibleClient with a TTL'd model cache) rather than an inline
        HTTP round-trip, so this endpoint and /api/chat agree on the served
        models. With LLM_PROVIDER=none (simulation only) there is nothing to
        probe and the LM Studio block reports inactive."""
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
        return process_upload(request, file, principal)

    @app.post("/api/upload-claim-file")
    def upload_claim_document(request: Request, claim_id: str = Form(...), file: UploadFile = File(...), principal=Depends(get_current_tenant)):  # noqa: B008
        require_permission_403(principal, "claims:write")
        require_claim_access_403(principal, claim_id)
        return process_upload(request, file, principal, claim_id)

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
        file_ext = file.filename.split(".")[-1].lower()
        if file_ext not in ["pdf", "docx", "xlsx", "xls", "txt"]:
            _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason="unsupported format")
            raise HTTPException(
                status_code=400,
                detail="Unsupported file format. Please upload PDF, DOCX, Excel, or Text documents."
            )

        content = file.file.read(runtime.settings.max_upload_bytes + 1)
        if len(content) > runtime.settings.max_upload_bytes:
            _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason="upload exceeds size limit")
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the maximum upload size of {runtime.settings.max_upload_bytes // (1024 * 1024)} MB.",
            )

        if runtime.settings.ingestion_mode == "async":
            if not content:
                _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason="empty document")
                raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")
            response = _enqueue_upload(content, file.filename, file_ext, claim_id=claim_id)
            _audit(request, principal, "upload", outcome="queued", filename=file.filename, claim_id=claim_id,
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
                text = parse_bounded(tmp_path, file_ext, runtime.settings.max_upload_bytes, runtime.settings.max_document_chars, runtime.settings.request_timeout_seconds)
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
                filename=file.filename,
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
            # contract), keeping sync and async response shapes compatible.
            job = runtime.ingestion_service.record_result(
                tenant_id=runtime.settings.tenant_id,
                filename=file.filename,
                content=content,
                document_id=str(doc_id),
            claim_id=claim_id,
            )

            _audit(request, principal, "upload", outcome="indexed", filename=file.filename, claim_id=claim_id,
                   file_size=file_size, chunks_count=parent_count, job_id=job.job_id)

            return {
                "filename": file.filename,
            "claim_id": claim_id,
                "chunks_count": parent_count,
                "total_time_ms": round(total_time, 1),
                "job_id": job.job_id,
                "status": job.status.value,
                "steps": {
                    "parsing_ms": round(parse_time, 1),
                    "db_storage_ms": round(db_time, 1)
                }
            }
        except HTTPException as exc:
            # Deliberate 4xx responses (empty document, corrupt file, scope
            # conflict) must not be re-wrapped by the generic handler into a 500.
            _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason=str(exc.detail))
            raise
        except ValueError as e:
            # safe_filename() rejections and the per-scope filename-conflict guard
            # in add_document() are client errors, not server failures.
            _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason=str(e))
            raise HTTPException(status_code=400, detail="Document processing failed.") from None
        except Exception:
            import traceback
            traceback.print_exc()
            _audit(request, principal, "upload", outcome="failed", filename=file.filename, reason="internal error")
            raise HTTPException(status_code=500, detail="Document processing failed.") from None
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


    @app.get("/api/documents/claim/{claim_id}", dependencies=[Depends(get_current_tenant)])
    def list_claim_documents(claim_id: str, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Lists all documents attached to a specific claim."""
        require_permission_403(principal, "claims:read")
        require_claim_access_403(principal, claim_id)
        return runtime.vector_store.get_claim_documents(claim_id)


    @app.get("/api/documents/content/{filename}", dependencies=[Depends(get_current_tenant)])
    def get_document_content(filename: str, version: str | None = None, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
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
        metadata = authorize_document(principal, filename)
        if version is not None and version != metadata["document_version"]:
            raise HTTPException(status_code=409, detail="This source has changed. Run the query again.")
        full_text = runtime.vector_store.get_document_content(filename)
        if not full_text:
            raise HTTPException(status_code=404, detail="Document content not found.")
        return {"filename": filename, "content": full_text}


    @app.get("/api/documents/download/{filename}", dependencies=[Depends(get_current_tenant)])
    def download_document(request: Request, filename: str, version: str | None = None, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
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
            metadata = authorize_document(principal, filename)
        except HTTPException as exc:
            _audit(request, principal, "download", outcome="not_found" if exc.status_code == 404 else "denied", filename=filename)
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
                _audit(request, principal, "download", outcome="not_found", filename=filename)
                raise HTTPException(status_code=404, detail="File not found.")
            url = blob_store.create_download_url(key, 3600)
            _audit(request, principal, "download", outcome="redirected", filename=filename)
            return RedirectResponse(url)
        from backend.blob_store import LocalDocumentBlobStore
        file_path = LocalDocumentBlobStore(runtime.vector_store.storage_dir)._path(metadata.get("storage_key") or filename)
        if not os.path.exists(file_path):
            _audit(request, principal, "download", outcome="not_found", filename=filename)
            raise HTTPException(status_code=404, detail="File not found.")
        _audit(request, principal, "download", outcome="served", filename=filename)
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
            safe_filename(req.filename)
        except ValueError:
            _audit(request, principal, "delete", outcome="failed", filename=req.filename, reason="invalid filename")
            raise HTTPException(status_code=400, detail="Invalid filename.") from None

        if runtime.settings.ingestion_mode == "async":
            job = runtime.ingestion_service.submit_delete(tenant_id=runtime.settings.tenant_id, filename=req.filename)
            _audit(request, principal, "delete", outcome="queued", filename=req.filename, job_id=job.job_id)
            return JSONResponse(
                status_code=202,
                content={
                    "job_id": job.job_id,
                    "status": job.status.value,
                    "filename": req.filename,
                },
            )

        deleted = runtime.vector_store.delete_document(req.filename)
        if not deleted:
            _audit(request, principal, "delete", outcome="not_found", filename=req.filename)
            raise HTTPException(status_code=404, detail="Document not found.")
        job = runtime.ingestion_service.record_result(
            tenant_id=runtime.settings.tenant_id,
            filename=req.filename,
            content=b"",
            status=IngestionStatus.deleted,
        )
        _audit(request, principal, "delete", outcome="deleted", filename=req.filename, job_id=job.job_id)
        return {
            "message": f"Successfully deleted '{req.filename}'.",
            "job_id": job.job_id,
            "status": job.status.value,
        }


    @app.post("/api/chat", dependencies=[Depends(get_current_tenant)])
    def chat_with_docs(request: Request, req: ChatRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """Answers a claims question using local Agentic RAG routing."""
        require_permission_403(principal, "documents:read")
        _rate_limit_429(principal)
        validate_chat(req)
        if req.claim_id:
            require_permission_403(principal, "claims:read")
            require_claim_access_403(principal, req.claim_id)
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
        _audit(
            request,
            principal,
            "chat",
            query=req.query,
            claim_id=req.claim_id,
            engine=req.engine,
            answer=result.get("answer"),
            sources=[source.get("filename") for source in result.get("sources", [])],
        )
        return project_result(request, result)


    @app.post("/api/chat/stream", dependencies=[Depends(get_current_tenant)])
    def chat_stream(request: Request, req: ChatRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
        """SSE counterpart to /api/chat: same auth/RBAC/rate-limit checks run
        synchronously before the generator starts (so a 401/403/429 is a normal
        HTTP status, not something swallowed once the stream has begun), then
        streams synthesis chunks and audits the assembled final answer exactly
        once, on the `final` event -- never per-chunk."""
        require_permission_403(principal, "documents:read")
        _rate_limit_429(principal)
        validate_chat(req)
        if req.claim_id:
            require_permission_403(principal, "claims:read")
            require_claim_access_403(principal, req.claim_id)

        def gen():
            for event in runtime.agentic_router.run_query_stream(
                    query_text=req.query, claim_id=req.claim_id, engine=req.engine,
                    embedding_engine=runtime._get_embedding_engine(), vector_store=runtime.vector_store,
                    reranking_engine=runtime._reranker, llm_client=runtime._llm_client, caps=runtime.settings):
                if event["type"] == "chunk":
                    yield f"data: {json.dumps({'text': event['text']})}\n\n"
                elif event["type"] == "final":
                    event = project_result(request, event)
                    _audit(request, principal, "chat",
                           query=req.query, claim_id=req.claim_id, engine=event.get("engine"),
                           answer=event.get("answer"),
                           sources=[s.get("filename") for s in (event.get("sources") or [])],
                           status=event.get("status"))
                    yield f"data: {json.dumps({k: v for k, v in event.items() if k != 'type'})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
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
        return _CONTENT_TYPES.get(file_ext, "application/octet-stream")


    @app.post("/api/eval/search", dependencies=[Depends(get_current_tenant)])
    def eval_search(req: SearchRequest, principal=Depends(get_current_tenant)):  # noqa: B008 - idiomatic FastAPI dependency injection
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
            require_claim_access_403(principal, req.claim_id)
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
        return {"mode": req.mode, "matches": matches}

    runtime._get_embedding_engine = _get_embedding_engine
    app.mount("/", StaticFiles(directory=str(REPO_ROOT / "frontend"), html=True), name="frontend")
