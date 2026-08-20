import os
# Force offline-only execution for Hugging Face transformers/sentence-transformers
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import shutil
import tempfile
import requests
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

# Import our RAG Engine classes
from backend.rag_engine import DocumentParser, TextChunker, EmbeddingEngine, SQLiteVectorStore, RerankingEngine, safe_filename
from backend.agentic_router import AgenticRAGRouter, CLAIMS_DATA

app = FastAPI(title="Local Insurance RAG System API")

# Restricted to localhost -- this app is 100% local-only by design (see
# CLAUDE.md's Critical Constraints), so a wildcard origin with credentials
# enabled had no upside and is a flagged anti-pattern regardless of current
# exposure. Add an origin here explicitly if the dev server ever needs to
# be reached from a different local port/host.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize engines
vector_store = SQLiteVectorStore()
embedding_engine = EmbeddingEngine()
reranking_engine = RerankingEngine()
agentic_router = AgenticRAGRouter()

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

@app.get("/api/status")
def get_status():
    """Checks the status of the local LLM servers."""
    lm_studio_models = get_loaded_models("http://127.0.0.1:1234")
    lm_studio_active = lm_studio_models is not None
    lm_studio_models = lm_studio_models or []

    return {
        "lm_studio": {
            "active": lm_studio_active,
            "url": "http://127.0.0.1:1234/v1",
            "models": lm_studio_models
        },
        "database": {
            "path": vector_store.db_path,
            "document_count": len(vector_store.get_all_documents())
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

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    """Uploads and processes a claim reference document."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in ["pdf", "docx", "xlsx", "xls", "txt"]:
        raise HTTPException(
            status_code=400, 
            detail="Unsupported file format. Please upload PDF, DOCX, Excel, or Text documents."
        )
        
    start_time = time.time()
    
    # Create temp file to read from
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        shutil.copyfileobj(file.file, tmp)
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
            )
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
            embedding_engine=embedding_engine,
            file_path=tmp_path
        )
        db_time = (time.time() - db_start) * 1000
        
        total_time = (time.time() - start_time) * 1000
        
        return {
            "filename": file.filename,
            "chunks_count": parent_count,
            "total_time_ms": round(total_time, 1),
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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/upload-claim-file")
async def upload_claim_document(claim_id: str = Form(...), file: UploadFile = File(...)):
    """Uploads and processes a document specifically for a given claim ID."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in ["pdf", "docx", "xlsx", "xls", "txt"]:
        raise HTTPException(
            status_code=400, 
            detail="Unsupported format. Upload PDF, DOCX, Excel, or Text."
        )
        
    start_time = time.time()
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
        
    try:
        parse_start = time.time()
        try:
            text = DocumentParser.parse(tmp_path, file_ext)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Could not read the uploaded file -- it may be corrupt, truncated, or password-protected."
            )
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
            embedding_engine=embedding_engine,
            claim_id=claim_id,
            file_path=tmp_path
        )
        db_time = (time.time() - db_start) * 1000
        total_time = (time.time() - start_time) * 1000
        
        return {
            "filename": file.filename,
            "claim_id": claim_id,
            "chunks_count": parent_count,
            "total_time_ms": round(total_time, 1),
            "steps": {
                "parsing_ms": round(parse_time, 1),
                "db_storage_ms": round(db_time, 1)
            }
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
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
    import sqlite3
    conn = sqlite3.connect(vector_store.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.content 
        FROM parent_chunks p
        JOIN documents d ON p.document_id = d.id
        WHERE d.filename = ?
        ORDER BY p.chunk_index ASC
    """, (filename,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        raise HTTPException(status_code=404, detail="Document content not found.")
        
    full_text = "\n\n".join([r[0] for r in rows])
    return {"filename": filename, "content": full_text}

@app.get("/api/documents/download/{filename}")
def download_document(filename: str):
    """Serves the physical document from the store's storage directory."""
    try:
        filename = safe_filename(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename.")
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
        embedding_engine=embedding_engine,
        vector_store=vector_store,
        reranking_engine=reranking_engine
    )
    return result

class SearchRequest(BaseModel):
    query: str
    claim_id: Optional[str] = None
    mode: str  # 'naive' (vector-only) | 'hybrid' (vector+FTS+RRF) | 'hybrid_rerank' (+ cross-encoder)
    top_k: int = 4

@app.post("/api/eval/search")
def eval_search(req: SearchRequest):
    """Raw retrieval endpoint (no LLM synthesis) for the eval harness to compare
    retrieval strategies. Not used by the frontend."""
    if req.mode not in ("naive", "hybrid", "hybrid_rerank"):
        raise HTTPException(status_code=400, detail="mode must be 'naive', 'hybrid', or 'hybrid_rerank'")

    query_emb = embedding_engine.embed_query(req.query)
    matches = vector_store.search_similarity(
        query_emb,
        req.query,
        claim_id=req.claim_id,
        reranking_engine=reranking_engine if req.mode == "hybrid_rerank" else None,
        top_k=req.top_k,
        use_fts=(req.mode != "naive"),
    )
    return {"mode": req.mode, "matches": matches}

# Mount frontend files at root
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
