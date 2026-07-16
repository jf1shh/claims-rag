import os
import shutil
import tempfile
import requests
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

# Import our RAG Engine classes
from backend.rag_engine import DocumentParser, TextChunker, EmbeddingEngine, SQLiteVectorStore

app = FastAPI(title="Local Insurance RAG System API")

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize engines
vector_store = SQLiteVectorStore()
embedding_engine = EmbeddingEngine()

# Ensure frontend directory exists
os.makedirs("frontend", exist_ok=True)

class ChatRequest(BaseModel):
    query: str
    engine: str  # 'lm-studio' or 'simulated'

class DeleteRequest(BaseModel):
    filename: str

def check_service_status(url: str) -> bool:
    try:
        response = requests.get(url, timeout=1.5)
        return response.status_code == 200
    except requests.RequestException:
        return False

def get_loaded_models(url: str) -> List[str]:
    try:
        response = requests.get(f"{url}/v1/models", timeout=1.5)
        if response.status_code == 200:
            data = response.json()
            return [m["id"] for m in data.get("data", [])]
    except requests.RequestException:
        pass
            
    return []

@app.get("/api/status")
def get_status():
    """Checks the status of the local LLM servers."""
    lm_studio_active = check_service_status("http://127.0.0.1:1234/v1/models")
    lm_studio_models = get_loaded_models("http://127.0.0.1:1234") if lm_studio_active else []
    
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

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    """Uploads and processes a claim reference document."""
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
        text = DocumentParser.parse(tmp_path, file_ext)
        parse_time = (time.time() - parse_start) * 1000
        
        if not text.strip():
            raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable.")
            
        # Step 2: Chunking
        chunk_start = time.time()
        chunks = TextChunker.chunk(text)
        chunk_time = (time.time() - chunk_start) * 1000
        
        if not chunks:
            raise HTTPException(status_code=400, detail="Failed to chunk document content.")
            
        # Step 3: Embeddings
        embed_start = time.time()
        embeddings = embedding_engine.embed_chunks(chunks)
        embed_time = (time.time() - embed_start) * 1000
        
        # Step 4: Save to vector database
        db_start = time.time()
        file_size = os.path.getsize(tmp_path)
        vector_store.add_document(
            filename=file.filename,
            file_type=file_ext,
            file_size=file_size,
            chunks=chunks,
            embeddings=embeddings
        )
        db_time = (time.time() - db_start) * 1000
        
        total_time = (time.time() - start_time) * 1000
        
        return {
            "filename": file.filename,
            "chunks_count": len(chunks),
            "total_time_ms": round(total_time, 1),
            "steps": {
                "parsing_ms": round(parse_time, 1),
                "chunking_ms": round(chunk_time, 1),
                "embedding_ms": round(embed_time, 1),
                "db_storage_ms": round(db_time, 1)
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/delete")
def delete_document(req: DeleteRequest):
    """Deletes a document from the store."""
    deleted = vector_store.delete_document(req.filename)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"message": f"Successfully deleted '{req.filename}'."}

@app.post("/api/chat")
def chat_with_docs(req: ChatRequest):
    """Answers a claims question using local RAG context."""
    logs = []
    
    # Step 1: Embed query
    start_time = time.time()
    query_emb = embedding_engine.embed_query(req.query)
    embed_time = (time.time() - start_time) * 1000
    logs.append(f"Generated query vector in {embed_time:.1f}ms")
    
    # Step 2: Query database
    search_start = time.time()
    matches = vector_store.search_similarity(query_emb, top_k=4)
    search_time = (time.time() - search_start) * 1000
    logs.append(f"Vector database similarity search completed in {search_time:.1f}ms (matched {len(matches)} passages)")
    
    # If no matches, return early warning
    if not matches:
        return {
            "answer": "I couldn't find any reference documents in the system. Please upload reference guidelines (PDF, DOCX, Excel) first in the sidebar.",
            "sources": [],
            "engine": "none",
            "pipeline_logs": logs
        }
        
    # Step 3: Construct context block
    context_blocks = []
    for idx, match in enumerate(matches):
        context_blocks.append(
            f"--- SOURCE {idx+1} | File: {match['filename']} (Sim: {match['score']:.3f}) ---\n{match['content']}\n"
        )
    context_text = "\n".join(context_blocks)
    
    # Step 4: Construct system prompt and user prompt
    system_prompt = (
        "You are an expert AI claims handler assistant. Your job is to answer the user's questions about insurance claims, "
        "policies, or guidelines using ONLY the provided reference documents. \n\n"
        "Rules:\n"
        "1. Base your answer strictly on the provided references.\n"
        "2. If the document content doesn't contain the answer, state that you cannot find it in the guidelines.\n"
        "3. Provide precise page/section/file references in your output if visible.\n"
        "4. Keep your answer clear, professional, and well-structured (use bullet points or headers if appropriate)."
    )
    
    user_prompt = (
        f"Retrieved Reference Guidelines:\n"
        f"=================================\n"
        f"{context_text}\n"
        f"=================================\n\n"
        f"Claims Handler Query: {req.query}\n\n"
        f"Answer:"
    )
    
    # Step 5: Route request based on selected engine
    answer = ""
    engine_used = req.engine
    llm_start = time.time()
    
    if req.engine == "lm-studio":
        try:
            # Query models to get the currently loaded model name
            models = get_loaded_models("http://127.0.0.1:1234")
            model_name = models[0] if models else "local-model"
            
            logs.append(f"Connecting to LM Studio on http://127.0.0.1:1234/v1 using model: {model_name}...")
            resp = requests.post(
                "http://127.0.0.1:1234/v1/chat/completions",
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1024
                },
                timeout=45
            )
            if resp.status_code == 200:
                answer = resp.json()["choices"][0]["message"]["content"]
                llm_time = (time.time() - llm_start) * 1000
                logs.append(f"LM Studio generated response in {llm_time:.1f}ms")
            else:
                raise Exception(f"LM Studio API returned error {resp.status_code}: {resp.text}")
        except Exception as e:
            logs.append(f"LM Studio call failed: {str(e)}. Falling back to simulation mode.")
            engine_used = "simulated"

    if engine_used == "simulated":
        logs.append("Running in Simulated Claims LLM Mode...")
        time.sleep(0.8) # Simulate processing delay for UI realism
        
        # Simple rule-based mock generation to show extraction working
        answer_parts = [
            "### [Simulated AI Claims Assistant Response]",
            "*(No local LLM running. Simulated response drafted from matched policy passages.)*",
            "\nBased on the matching references found in the system, here are the relevant details:\n"
        ]
        
        for idx, match in enumerate(matches[:2]): # Use top 2 matches
            snippet = match['content'][:300].replace('\n', ' ')
            if len(match['content']) > 300:
                snippet += "..."
            answer_parts.append(
                f"- **From {match['filename']}** (Match Score: {match['score']:.2f}):\n"
                f"  > \"{snippet}\"\n"
            )
            
        answer_parts.append(
            "\n*To enable true natural language reasoning, launch LM Studio and toggle the engine selection above.*"
        )
        answer = "\n".join(answer_parts)
        llm_time = (time.time() - llm_start) * 1000
        logs.append(f"Simulation completed in {llm_time:.1f}ms")
        
    return {
        "answer": answer,
        "sources": [
            {
                "filename": m["filename"],
                "file_type": m["file_type"],
                "content": m["content"],
                "score": round(m["score"], 3)
            }
            for m in matches
        ],
        "engine": engine_used,
        "pipeline_logs": logs
    }

# Mount frontend files at root
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
