import os
import sqlite3
import time
import json
import numpy as np
import pypdf
import docx
import pandas as pd
from sentence_transformers import SentenceTransformer

DB_PATH = "rag_store.db"

class DocumentParser:
    @staticmethod
    def parse(file_path, file_type):
        """Extracts text content from PDF, DOCX, XLSX, and TXT files."""
        if file_type == "pdf":
            return DocumentParser._parse_pdf(file_path)
        elif file_type == "docx":
            return DocumentParser._parse_docx(file_path)
        elif file_type in ["xlsx", "xls"]:
            return DocumentParser._parse_excel(file_path)
        elif file_type == "txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        else:
            raise ValueError(f"Unsupported file type: {file_type}")

    @staticmethod
    def _parse_pdf(file_path):
        reader = pypdf.PdfReader(file_path)
        text_parts = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n\n".join(text_parts)

    @staticmethod
    def _parse_docx(file_path):
        doc = docx.Document(file_path)
        text_parts = []
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_parts.append(paragraph.text)
        
        # Parse tables
        for table in doc.tables:
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_data:
                    text_parts.append(" | ".join(row_data))
        
        return "\n\n".join(text_parts)

    @staticmethod
    def _parse_excel(file_path):
        xls = pd.ExcelFile(file_path)
        text_parts = []
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            if not df.empty:
                text_parts.append(f"--- Sheet: {sheet_name} ---")
                text_parts.append(df.to_string(index=False))
        return "\n\n".join(text_parts)


class TextChunker:
    @staticmethod
    def chunk(text, chunk_size=800, chunk_overlap=150):
        """Splits text into smaller, overlapping chunks."""
        chunks = []
        text_len = len(text)
        start = 0
        
        if text_len == 0:
            return []

        while start < text_len:
            end = min(start + chunk_size, text_len)
            
            # Find a clean boundary (whitespace or newline)
            if end < text_len:
                last_space = text.rfind(' ', end - 100, end)
                if last_space != -1:
                    end = last_space
            
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
                
            start = end - chunk_overlap
            
            # Prevent infinite loops or tiny final fragments
            if start >= text_len - chunk_overlap or end >= text_len:
                # Get the remaining portion if it is substantial
                remaining = text[end - chunk_overlap:].strip()
                if remaining and len(remaining) > 50 and remaining not in chunks:
                    chunks.append(remaining)
                break
                
        return chunks


class EmbeddingEngine:
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        print(f"Loading embedding model '{model_name}' (cached locally after first download)...")
        # Downloads model on first run, runs completely locally on CPU or GPU
        self.model = SentenceTransformer(model_name)
        print("Model loaded successfully.")

    def embed_chunks(self, chunks):
        """Generates embeddings for list of text chunks."""
        if not chunks:
            return []
        embeddings = self.model.encode(chunks, show_progress_bar=False)
        return embeddings

    def embed_query(self, query):
        """Generates embedding for a single search query."""
        return self.model.encode(query, show_progress_bar=False)


class SQLiteVectorStore:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Documents table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE,
                file_type TEXT,
                file_size INTEGER,
                uploaded_at TEXT
            )
        """)
        
        # Chunks table with embedding stored as BLOB (float32 array)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                chunk_index INTEGER,
                content TEXT,
                embedding BLOB,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        """)
        
        conn.commit()
        conn.close()

    def add_document(self, filename, file_type, file_size, chunks, embeddings):
        """Inserts document record and its chunk embeddings into the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Delete if exists (to overwrite)
            cursor.execute("SELECT id FROM documents WHERE filename = ?", (filename,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("DELETE FROM documents WHERE id = ?", (existing[0],))
            
            # Insert document
            uploaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO documents (filename, file_type, file_size, uploaded_at) VALUES (?, ?, ?, ?)",
                (filename, file_type, file_size, uploaded_at)
            )
            doc_id = cursor.lastrowid
            
            # Insert chunks
            for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                # Convert float32 numpy array to raw bytes
                emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
                cursor.execute(
                    "INSERT INTO chunks (document_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
                    (doc_id, idx, chunk_text, emb_bytes)
                )
            
            conn.commit()
            return doc_id
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def delete_document(self, filename):
        """Deletes a document and cascade deletes its chunks."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM documents WHERE filename = ?", (filename,))
            row = cursor.fetchone()
            if row:
                doc_id = row[0]
                cursor.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
                cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                conn.commit()
                return True
            return False
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def get_all_documents(self):
        """Returns list of all uploaded documents."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT filename, file_type, file_size, uploaded_at FROM documents ORDER BY uploaded_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "filename": r[0],
                "file_type": r[1],
                "file_size": r[2],
                "uploaded_at": r[3]
            }
            for r in rows
        ]

    def search_similarity(self, query_embedding, top_k=5):
        """Computes cosine similarity of query embedding against all stored chunks in a vectorized manner."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Join chunks with documents to get filenames
        cursor.execute("""
            SELECT c.content, c.embedding, d.filename, d.file_type 
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return []
            
        contents = []
        filenames = []
        file_types = []
        vectors = []
        
        for content, emb_bytes, filename, file_type in rows:
            chunk_vector = np.frombuffer(emb_bytes, dtype=np.float32)
            # Safeguard shape check for 384-dimension all-MiniLM-L6-v2 vector
            if chunk_vector.shape[0] == 384:
                vectors.append(chunk_vector)
                contents.append(content)
                filenames.append(filename)
                file_types.append(file_type)
        
        if not vectors:
            return []
            
        # Convert list of vectors to 2D numpy array (N, 384)
        matrix = np.vstack(vectors)
        query = np.array(query_embedding, dtype=np.float32)
        
        # Compute dot products in a single vectorized operation: shape (N,)
        dot_products = np.dot(matrix, query)
        
        # Compute norms
        matrix_norms = np.linalg.norm(matrix, axis=1)
        query_norm = np.linalg.norm(query)
        
        # Avoid division by zero
        matrix_norms[matrix_norms == 0] = 1.0
        query_norm_val = query_norm if query_norm != 0 else 1.0
        
        # Vectorized cosine similarity
        similarities = dot_products / (matrix_norms * query_norm_val)
        
        results = []
        for i in range(len(similarities)):
            results.append({
                "content": contents[i],
                "filename": filenames[i],
                "file_type": file_types[i],
                "score": float(similarities[i])
            })
            
        # Sort by similarity score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
