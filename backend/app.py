import os
# Force offline-only execution for Hugging Face transformers/sentence-transformers
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import tempfile
import requests
import time
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

# Import our RAG Engine classes
from backend.rag_engine import DocumentParser, EmbeddingEngine, SQLiteVectorStore, RerankingEngine, safe_filename, _blob_key
from backend.agentic_router import AgenticRAGRouter, CLAIMS_DATA

from config import get_settings
from backend.health import live_status, ready_status

app = FastAPI(title="Local Insurance RAG System API")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# Restricted to localhost -- this app is 100% local-only by design (see
# CLAUDE.md's Critical Constraints), so a wildcard origin with credentials
# enabled had no upside and is a flagged anti-pattern regardless of current
# exposure. Add an origin here explicitly if the dev server ever needs to
# be reached from a different local port/host.
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the lightweight storage adapter at import time. ML engines remain
# lazy so importing the ASGI app does not require local model files. When an
# object store (S3) is configured, add/delete/serve route source bytes through
# it; otherwise the legacy filesystem storage_dir path is used unchanged.
from app_factory import _build_blob_store  # noqa: E402

_vector_blob_store = _build_blob_store(settings)
vector_store = SQLiteVectorStore(
    db_path=str(settings.rag_db_path),
    storage_dir=str(settings.stored_documents_dir),
    blob_store=_vector_blob_store,
)
embedding_engine = None
reranking_engine = None
agentic_router = AgenticRAGRouter()


def _get_embedding_engine():
    global embedding_engine
    if embedding_engine is None:
        embedding_engine = EmbeddingEngine(model_name=settings.embedding_model)
    return embedding_engine


def _get_reranking_engine():
    global reranking_engine
    if reranking_engine is None:
        reranking_engine = RerankingEngine(model_name=settings.reranker_model)
    return reranking_engine

# Phase 3 async ingestion (milestone 3.2): durable job records + queue.
# Sync mode (default) keeps the historical inline upload pipeline; async mode
# returns 202 + job_id and the worker parses/embeds/indexes off the request
# thread. In async mode the upload endpoint stages source bytes in the blob
# store (a local ingest_queue under stored_documents for the filesystem
# provider, or the S3 adapter) and an in-process worker consumes the queue so
# local async dev works end-to-end; SQS deployments run the worker as its own
# process via build_ingestion_worker().
from backend.ingestion import IngestionService  # noqa: E402
from backend.job_store import SqliteJobStore  # noqa: E402

ingestion_job_store = SqliteJobStore(str(settings.jobs_db_path))
_async_blob_store = None
_ingestion_worker = None
if settings.ingestion_mode == "async":
    from app_factory import _build_queue  # noqa: E402
    from backend.blob_store import LocalDocumentBlobStore  # noqa: E402
    from backend.ingestion_worker import build_ingestion_worker  # noqa: E402

    _async_blob_store = _vector_blob_store or LocalDocumentBlobStore(
        os.path.join(str(settings.stored_documents_dir), "ingest_queue")
    )
    ingestion_service = IngestionService(
        queue=_build_queue(settings), job_store=ingestion_job_store
    )
    _ingestion_worker = build_ingestion_worker(
        settings, vector_store, _async_blob_store, _get_embedding_engine, job_store=ingestion_job_store
    )
    _ingestion_worker.start()
else:
    ingestion_service = IngestionService(job_store=ingestion_job_store)

# Ensure frontend directory exists
os.makedirs("frontend", exist_ok=True)

class ChatRequest(BaseModel):
    query: str
    engine: str  # 'lm-studio' or 'simulated'
    claim_id: Optional[str] = None

class DeleteRequest(BaseModel):
    filename: str

def get_loaded_models(url: str) -> Optional[List[str]]:
    """Returns the loaded model ids, or None if LM Studio is unreachable.
    None-vs-empty-list distinguishes 'server down' from 'server up with no
    model loaded', so the status check needs only this single round-trip."""
    try:
        response = requests.get(f"{url}/v1/models", timeout=1.5)
        if response.status_code == 200:
            data = response.json()
            return [m["id"] for m in data.get("data", [])]
    except requests.RequestException:
        pass
    return None

@app.get("/health/live")
def health_live():
    return live_status()


@app.get("/health/ready")
def health_ready():
    payload, status_code = ready_status({"database": lambda: vector_store._connect().close()})
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/api/status")
def get_status():
    """Checks the status of the local LLM servers."""
    lm_studio_models = get_loaded_models(settings.llm_base_url)
    lm_studio_active = lm_studio_models is not None
    lm_studio_models = lm_studio_models or []

    return {
        "lm_studio": {
            "active": lm_studio_active,
            "url": f"{settings.llm_base_url}/v1",
            "models": lm_studio_models
        },
        "database": {
            "path": vector_store.db_path,
            "document_count": len(vector_store.get_all_documents())
        },
        "ingestion": {
            "mode": settings.ingestion_mode
        }
    }

@app.get("/api/documents")
def list_documents():
    """Lists all processed documents."""
    return vector_store.get_all_documents()

@app.get("/api/claims")
def list_claims():
    """Serves the demo claims queue -- the single source of truth CLAIMS_DATA
    (backend/agentic_router.py) the agentic router already grounds claim-scoped
    answers in, so the frontend's claim cards can't drift out of sync with it."""
    return CLAIMS_DATA

@app.get("/api/jobs/{job_id}")
def get_ingestion_job(job_id: str):
    """Returns the durable ingestion job record for an upload (Phase 3.2).
    The frontend polls this while an async upload runs, so the UI reflects
    real pipeline state instead of a fake progress bar. Tenant-guarded: a job
    whose tenant does not match the configured tenant is not readable."""
    try:
        return ingestion_service.get(job_id, settings.tenant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found.") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="Forbidden.") from None

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):  # noqa: B008 - idiomatic FastAPI required form

    """Uploads and processes a claim reference document.

    Sync mode (default): parses and indexes inline, returning the timing
    payload plus job_id/status so the response contract is compatible with the
    async shape (Phase 3.2). Async mode (INGESTION_MODE=async): validates
    cheaply (filename/extension/empty), stages the bytes in the blob store,
    returns 202 + job_id, and the ingestion worker parses/embeds/indexes off
    the request thread -- the frontend polls GET /api/jobs/{job_id} for real
    pipeline state.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in ["pdf", "docx", "xlsx", "xls", "txt"]:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload PDF, DOCX, Excel, or Text documents."
        )

    content = await file.read()

    if settings.ingestion_mode == "async":
        if not content:
            raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")
        return _enqueue_upload(content, file.filename, file_ext, claim_id=None)

    start_time = time.time()

    # Create temp file to read from
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Step 1: Text extraction
        parse_start = time.time()
        try:
            text = DocumentParser.parse(tmp_path, file_ext)
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
        doc_id, parent_count = vector_store.add_document(
            filename=file.filename,
            file_type=file_ext,
            file_size=file_size,
            text=text,
            embedding_engine=_get_embedding_engine(),
            file_path=tmp_path
        )
        db_time = (time.time() - db_start) * 1000

        total_time = (time.time() - start_time) * 1000

        # Record the finished job so the response carries a job_id (the async
        # contract), keeping sync and async response shapes compatible.
        job = ingestion_service.record_result(
            tenant_id=settings.tenant_id,
            filename=file.filename,
            content=content,
            document_id=str(doc_id),
        )

        return {
            "filename": file.filename,
            "chunks_count": parent_count,
            "total_time_ms": round(total_time, 1),
            "job_id": job.job_id,
            "status": job.status.value,
            "steps": {
                "parsing_ms": round(parse_time, 1),
                "db_storage_ms": round(db_time, 1)
            }
        }
    except HTTPException:
        # Deliberate 4xx responses (empty document, corrupt file, scope
        # conflict) must not be re-wrapped by the generic handler into a 500.
        raise
    except ValueError as e:
        # safe_filename() rejections and the per-scope filename-conflict guard
        # in add_document() are client errors, not server failures.
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from None
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/upload-claim-file")
async def upload_claim_document(claim_id: str = Form(...), file: UploadFile = File(...)):  # noqa: B008 - idiomatic FastAPI required form

    """Uploads and processes a document specifically for a given claim ID.
    Same sync/async split as /api/upload (see its docstring)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in ["pdf", "docx", "xlsx", "xls", "txt"]:
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Upload PDF, DOCX, Excel, or Text."
        )

    content = await file.read()

    if settings.ingestion_mode == "async":
        if not content:
            raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")
        return _enqueue_upload(content, file.filename, file_ext, claim_id=claim_id)

    start_time = time.time()
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        parse_start = time.time()
        try:
            text = DocumentParser.parse(tmp_path, file_ext)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Could not read the uploaded file -- it may be corrupt, truncated, or password-protected."
            ) from None
        parse_time = (time.time() - parse_start) * 1000

        if not text.strip():
            raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")

        db_start = time.time()
        file_size = os.path.getsize(tmp_path)
        doc_id, parent_count = vector_store.add_document(
            filename=file.filename,
            file_type=file_ext,
            file_size=file_size,
            text=text,
            embedding_engine=_get_embedding_engine(),
            claim_id=claim_id,
            file_path=tmp_path
        )
        db_time = (time.time() - db_start) * 1000
        total_time = (time.time() - start_time) * 1000

        job = ingestion_service.record_result(
            tenant_id=settings.tenant_id,
            filename=file.filename,
            content=content,
            claim_id=claim_id,
            document_id=str(doc_id),
        )

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
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e)) from None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.get("/api/documents/claim/{claim_id}")
def list_claim_documents(claim_id: str):
    """Lists all documents attached to a specific claim."""
    return vector_store.get_claim_documents(claim_id)

@app.get("/api/documents/content/{filename}")
def get_document_content(filename: str):
    """Fetches the full text content of a document by joining all its parent chunks."""
    try:
        filename = safe_filename(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename.") from None
    # Backend-neutral: the store joins its own parent chunks. (This previously
    # opened an inline sqlite connection on vector_store.db_path, which could
    # not work on a Postgres backend.)
    full_text = vector_store.get_document_content(filename)
    if not full_text:
        raise HTTPException(status_code=404, detail="Document content not found.")
    return {"filename": filename, "content": full_text}

@app.get("/api/documents/download/{filename}")
def download_document(filename: str):
    """Serves the physical document. With an object store configured this is a
    redirect to a presigned URL; otherwise the file streams from the local
    storage directory. Both paths keep the source bytes behind the store's
    serving abstraction rather than exposing a raw filesystem path."""
    try:
        filename = safe_filename(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename.") from None
    blob_store = getattr(vector_store, "blob_store", None)
    if blob_store is not None:
        # The scope (claim vs global) is encoded in the blob key; the store
        # resolves it by looking the document up in the DB.
        key = vector_store.get_blob_key(filename)
        if key is None:
            raise HTTPException(status_code=404, detail="File not found.")
        url = blob_store.create_download_url(key, 3600)
        return RedirectResponse(url)
    file_path = os.path.join(vector_store.storage_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(file_path)

@app.post("/api/delete")
def delete_document(req: DeleteRequest):
    """Deletes a document from the store."""
    deleted = vector_store.delete_document(req.filename)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"message": f"Successfully deleted '{req.filename}'."}

@app.post("/api/chat")
def chat_with_docs(req: ChatRequest):
    """Answers a claims question using local Agentic RAG routing."""
    result = agentic_router.run_query(
        query_text=req.query,
        claim_id=req.claim_id,
        engine=req.engine,
        embedding_engine=_get_embedding_engine(),
        vector_store=vector_store,
        reranking_engine=_get_reranking_engine()
    )
    return result

def _enqueue_upload(content: bytes, filename: str, file_ext: str, claim_id: str | None) -> JSONResponse:
    """Async-mode upload: stage the source bytes in the blob store, enqueue an
    ingestion job, and return the 202 response the frontend polls against."""
    _async_blob_store.put(_blob_key(claim_id, filename), content, _content_type(file_ext))
    job = ingestion_service.submit(
        tenant_id=settings.tenant_id,
        filename=filename,
        content=content,
        claim_id=claim_id,
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


_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "txt": "text/plain",
}


def _content_type(file_ext: str) -> str:
    return _CONTENT_TYPES.get(file_ext, "application/octet-stream")


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

@app.post("/api/eval/search")
def eval_search(req: SearchRequest):
    """Raw retrieval endpoint (no LLM synthesis) for the eval harness to compare
    retrieval strategies. Not used by the frontend."""
    if req.mode not in ("naive", "hybrid", "hybrid_rerank"):
        raise HTTPException(status_code=400, detail="mode must be 'naive', 'hybrid', or 'hybrid_rerank'")

    query_emb = _get_embedding_engine().embed_query(req.query)
    matches = vector_store.search_similarity(
        query_emb,
        req.query,
        claim_id=req.claim_id,
        reranking_engine=_get_reranking_engine() if req.mode == "hybrid_rerank" else None,
        top_k=req.top_k,
        use_fts=(req.mode != "naive"),
    )
    return {"mode": req.mode, "matches": matches}

# Mount frontend files at root
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
