import os
import sys
import numpy as np

# Add parent directory to path so we can import from backend
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend.rag_engine import DocumentParser, TextChunker, EmbeddingEngine, SQLiteVectorStore

def run_test():
    print("=== Testing Auto Claims Local RAG Ingestion & Vector Search Pipeline ===")
    
    # 1. Initialize Engines
    print("1. Initializing engines...")
    vector_store = SQLiteVectorStore()
    embedding_engine = EmbeddingEngine()
    
    # 2. Process California Regulations PDF Ingestion
    print("2. Ingesting California_Auto_Claims_Regulations.pdf...")
    pdf_path = "sample_guidelines/California_Auto_Claims_Regulations.pdf"
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found. Please run generate_auto_pdfs.py first.")
        return
        
    text_pdf = DocumentParser.parse(pdf_path, "pdf")
    chunks_pdf = TextChunker.chunk(text_pdf)
    embeddings_pdf = embedding_engine.embed_chunks(chunks_pdf)
    
    vector_store.add_document(
        filename="California_Auto_Claims_Regulations.pdf",
        file_type="pdf",
        file_size=os.path.getsize(pdf_path),
        chunks=chunks_pdf,
        embeddings=embeddings_pdf
    )
    print(f"   Indexed PDF: {len(chunks_pdf)} chunks saved.")
    
    # 3. Process SOP Labor Rates PDF Ingestion
    print("3. Ingesting SOP_Auto_Repair_Labor_Rates.pdf...")
    labor_path = "sample_guidelines/SOP_Auto_Repair_Labor_Rates.pdf"
    if not os.path.exists(labor_path):
        print(f"Error: {labor_path} not found.")
        return
        
    text_labor = DocumentParser.parse(labor_path, "pdf")
    chunks_labor = TextChunker.chunk(text_labor)
    embeddings_labor = embedding_engine.embed_chunks(chunks_labor)
    
    vector_store.add_document(
        filename="SOP_Auto_Repair_Labor_Rates.pdf",
        file_type="pdf",
        file_size=os.path.getsize(labor_path),
        chunks=chunks_labor,
        embeddings=embeddings_labor
    )
    print(f"   Indexed PDF: {len(chunks_labor)} chunks saved.")
    
    # 4. Run Search Query
    query = "What is the hourly labor rate for mechanical work or frame alignment?"
    print(f"\n4. Running similarity search for: '{query}'")
    query_emb = embedding_engine.embed_query(query)
    
    matches = vector_store.search_similarity(query_emb, top_k=3)
    
    print("\nSearch Results (Top Matches):")
    print("==========================================================================")
    for idx, match in enumerate(matches):
        print(f"Match {idx+1} | Score: {match['score']:.4f} | File: {match['filename']}")
        print(f"Content:\n{match['content']}")
        print("--------------------------------------------------------------------------")
    print("==========================================================================")
    
    if len(matches) > 0 and "SOP_Auto_Repair_Labor_Rates.pdf" in matches[0]["filename"]:
        print("Auto Claims Vectorized Pipeline verification SUCCESSFUL!")
    else:
        print("Pipeline verification finished (check match files and scores).")

if __name__ == "__main__":
    run_test()
