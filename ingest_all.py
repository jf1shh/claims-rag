import os
import sys
import time

# Add parent directory to path so we can import from backend
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend.rag_engine import DocumentParser, TextChunker, EmbeddingEngine, SQLiteVectorStore

def ingest_all():
    print("=== Local RAG Batch Ingestion Script ===")
    
    # 1. Initialize Engines
    print("Initializing engines...")
    vector_store = SQLiteVectorStore()
    embedding_engine = EmbeddingEngine()
    
    # Clean up DB for fresh start
    print("Clearing existing records for a clean batch index...")
    conn = vector_store.db_path
    import sqlite3
    db = sqlite3.connect(conn)
    c = db.cursor()
    c.execute("DELETE FROM chunks")
    c.execute("DELETE FROM documents")
    db.commit()
    db.close()
    print("Database cleared.")

    folder_path = "sample_guidelines"
    if not os.path.exists(folder_path):
        print(f"Error: Folder '{folder_path}' not found.")
        return
        
    files = [f for f in os.listdir(folder_path) if f.split('.')[-1].lower() in ["pdf", "docx", "xlsx", "xls", "txt"]]
    print(f"Found {len(files)} files to index inside '{folder_path}'.\n")
    
    total_start = time.time()
    
    for idx, filename in enumerate(files):
        file_path = os.path.join(folder_path, filename)
        file_ext = filename.split('.')[-1].lower()
        
        # In pypdf, docx, pandas, etc., we treat txt as plain text
        # Let's add simple txt parsing fallback directly in this script if needed
        # In rag_engine.py, we only have pdf, docx, xlsx. Let's make sure we handle .txt by adding it to parser 
        # or just reading it as plain text. Let's see: we can read plain text easily.
        
        file_size = os.path.getsize(file_path)
        print(f"[{idx+1}/{len(files)}] Processing {filename} ({file_size / 1024:.1f} KB)...")
        
        start = time.time()
        
        try:
            # Parse text
            if file_ext == "txt":
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            else:
                text = DocumentParser.parse(file_path, file_ext)
                
            if not text.strip():
                print(f"   ⚠️ Warning: Document '{filename}' is empty, skipping.")
                continue
                
            # Chunk text
            chunks = TextChunker.chunk(text)
            if not chunks:
                print(f"   ⚠️ Warning: No chunks generated for '{filename}', skipping.")
                continue
                
            # Embed chunks
            embeddings = embedding_engine.embed_chunks(chunks)
            
            # Save to SQLite Vector Store
            # Note: if it is txt, we can treat its type as 'txt'
            vector_store.add_document(
                filename=filename,
                file_type=file_ext,
                file_size=file_size,
                chunks=chunks,
                embeddings=embeddings
            )
            
            elapsed = time.time() - start
            print(f"   Indexed: {len(chunks)} chunks in {elapsed:.2f} seconds.")
            
        except Exception as e:
            print(f"   ❌ Error processing '{filename}': {str(e)}")
            
    total_elapsed = time.time() - total_start
    print(f"\n=== Batch Ingestion Complete! ===")
    print(f"Indexed {len(vector_store.get_all_documents())} documents successfully.")
    print(f"Total processing time: {total_elapsed:.2f} seconds.")

if __name__ == "__main__":
    ingest_all()
