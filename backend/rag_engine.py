import os
# Force offline-only execution for Hugging Face transformers/sentence-transformers
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import sqlite3
import time
import json
import numpy as np
import pypdf
import docx
import pandas as pd
# NOTE: sentence_transformers/torch are imported lazily inside EmbeddingEngine
# and RerankingEngine so that the pure-numpy SQLiteVectorStore (and document
# parsing) can be imported and exercised without loading the heavy ML stack.

DB_PATH = "rag_store.db"


def safe_filename(filename):
    """Strips any directory components so a client-supplied filename can't
    escape stored_documents/ via path traversal (../, ..\\, or an absolute
    path). Must be applied before any filename touches a filesystem path --
    os.path.basename handles both '/' and '\\' on Windows; on POSIX there is
    no backslash-traversal risk in the first place.
    """
    name = os.path.basename((filename or "").strip())
    if not name or name in (".", ".."):
        raise ValueError(f"Invalid filename: {filename!r}")
    return name


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

            if end >= text_len:
                # This chunk already reaches the end of the text -- there's no
                # tail left to add. (Previously a "prevent infinite loops"
                # fallback ran unconditionally here too and re-appended an
                # overlapping duplicate of the tail just added -- every short
                # document, and the end of every long one, got a redundant
                # near-duplicate chunk. Confirmed via rag_store.db: e.g.
                # custom_equipment_receipts_Chen.xlsx, ~350 chars, produced 2
                # parent chunks -- the full text, then its own last ~200 chars
                # again.)
                break

            next_start = end - chunk_overlap
            if next_start <= start:
                # Boundary snapping (the whitespace search above) left no
                # forward progress -- stop rather than loop without advancing.
                break
            start = next_start

        return chunks


class EmbeddingEngine:
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        print(f"Loading embedding model '{model_name}' (cached locally)...")
        self.model = SentenceTransformer(model_name)
        print("Model loaded successfully.")

    def embed_chunks(self, chunks):
        """Generates embeddings for list of text chunks."""
        if not chunks:
            return []
        return self.model.encode(chunks, show_progress_bar=False)

    def embed_query(self, query):
        """Generates embedding for a single search query."""
        return self.model.encode(query, show_progress_bar=False)


class RerankingEngine:
    def __init__(self, model_name="cross-encoder/ms-marco-MiniLM-L-6-v2"):
        from sentence_transformers import CrossEncoder
        print(f"Loading reranking model '{model_name}' on CPU...")
        self.model = CrossEncoder(model_name, device="cpu")
        print("Reranking model loaded successfully.")

    def rerank(self, query, passages, top_k=4):
        """Scores passages against query and returns top_k sorted list."""
        if not passages:
            return []
        
        pairs = [(query, p["content"]) for p in passages]
        scores = self.model.predict(pairs, show_progress_bar=False)
        
        for idx, score in enumerate(scores):
            passages[idx]["rerank_score"] = float(score)
            
        passages.sort(key=lambda x: x["rerank_score"], reverse=True)
        return passages[:top_k]


class SQLiteVectorStore:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        # In-memory, pre-normalized embedding index. Built lazily on first
        # search and invalidated whenever documents are added/removed. This
        # avoids re-reading every embedding BLOB and recomputing corpus norms
        # on each query (the primary CPU bottleneck as the corpus scales).
        self._vector_cache = None
        self._init_db()

    def _invalidate_vector_cache(self):
        self._vector_cache = None

    def _build_vector_cache(self):
        """Loads all child embeddings once, L2-normalizes them, and caches the
        matrix plus parallel metadata arrays for fast masked cosine search."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.embedding, p.content, d.filename, d.file_type, p.id, d.claim_id
            FROM child_chunks c
            JOIN parent_chunks p ON c.parent_id = p.id
            JOIN documents d ON p.document_id = d.id
        """)
        rows = cursor.fetchall()
        conn.close()

        vectors, contents, filenames, file_types, parent_ids, claim_ids = [], [], [], [], [], []
        for emb_bytes, content, filename, file_type, p_id, claim_id in rows:
            vec = np.frombuffer(emb_bytes, dtype=np.float32)
            if vec.shape[0] != 384:
                continue
            vectors.append(vec)
            contents.append(content)
            filenames.append(filename)
            file_types.append(file_type)
            parent_ids.append(p_id)
            claim_ids.append(claim_id)

        if vectors:
            matrix = np.vstack(vectors).astype(np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            matrix = matrix / norms  # pre-normalized so cosine == dot product
        else:
            matrix = np.zeros((0, 384), dtype=np.float32)

        self._vector_cache = {
            "matrix": matrix,
            "contents": contents,
            "filenames": filenames,
            "file_types": file_types,
            "parent_ids": parent_ids,
            "claim_ids": np.array(claim_ids, dtype=object),
            "global_mask": np.array([cid is None for cid in claim_ids], dtype=bool),
        }
        return self._vector_cache

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Documents table (with claim_id tag)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE,
                file_type TEXT,
                file_size INTEGER,
                uploaded_at TEXT,
                claim_id TEXT
            )
        """)
        
        # Parent chunks table (larger text blocks for context)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parent_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                chunk_index INTEGER,
                content TEXT,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        """)
        
        # Child chunks table (smaller text blocks for vector embeddings)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS child_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER,
                content TEXT,
                embedding BLOB,
                FOREIGN KEY (parent_id) REFERENCES parent_chunks (id) ON DELETE CASCADE
            )
        """)
        
        # FTS5 Virtual Table for keyword search on parent chunks
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS parent_chunks_fts USING fts5(
                content,
                tokenize='porter'
            )
        """)
        
        conn.commit()
        conn.close()

    def add_document(self, filename, file_type, file_size, text, embedding_engine, claim_id=None, file_path=None):
        """Inserts document, parent chunks, FTS index, child chunks and their embeddings; copies physical file to disk."""
        import shutil
        filename = safe_filename(filename)
        os.makedirs("stored_documents", exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Delete if exists (to overwrite)
            cursor.execute("SELECT id FROM documents WHERE filename = ?", (filename,))
            existing = cursor.fetchone()
            if existing:
                doc_id = existing[0]
                # Delete FTS index
                cursor.execute("DELETE FROM parent_chunks_fts WHERE rowid IN (SELECT id FROM parent_chunks WHERE document_id = ?)", (doc_id,))
                # Delete document
                cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                
                # Delete old stored document file if exists
                old_file = os.path.abspath(os.path.join("stored_documents", filename))
                if os.path.exists(old_file):
                    if not file_path or os.path.abspath(file_path) != old_file:
                        os.remove(old_file)
            
            # Insert document
            uploaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO documents (filename, file_type, file_size, uploaded_at, claim_id) VALUES (?, ?, ?, ?, ?)",
                (filename, file_type, file_size, uploaded_at, claim_id)
            )
            doc_id = cursor.lastrowid
            
            # Save physical file on disk
            dest_path = os.path.abspath(os.path.join("stored_documents", filename))
            if file_path and os.path.exists(file_path):
                src_abs = os.path.abspath(file_path)
                if src_abs != dest_path:
                    shutil.copy2(file_path, dest_path)
            else:
                if not os.path.exists(dest_path):
                    with open(dest_path, "w", encoding="utf-8", errors="ignore") as f:
                        f.write(text)
            
            # 1. Generate Parent Chunks
            parent_chunks = TextChunker.chunk(text, chunk_size=1200, chunk_overlap=200)
            
            # 2. Process each Parent Chunk
            for p_idx, p_text in enumerate(parent_chunks):
                # Insert parent
                cursor.execute(
                    "INSERT INTO parent_chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                    (doc_id, p_idx, p_text)
                )
                p_id = cursor.lastrowid
                
                # Insert parent into FTS5
                cursor.execute(
                    "INSERT INTO parent_chunks_fts (rowid, content) VALUES (?, ?)",
                    (p_id, p_text)
                )
                
                # Generate Child Chunks for this parent
                child_chunks = TextChunker.chunk(p_text, chunk_size=250, chunk_overlap=50)
                if not child_chunks:
                    continue
                
                # Generate embeddings for children
                embeddings = embedding_engine.embed_chunks(child_chunks)
                
                # Insert children and embeddings
                for c_text, embedding in zip(child_chunks, embeddings):
                    emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
                    cursor.execute(
                        "INSERT INTO child_chunks (parent_id, content, embedding) VALUES (?, ?, ?)",
                        (p_id, c_text, emb_bytes)
                    )
            
            conn.commit()
            self._invalidate_vector_cache()
            return doc_id, len(parent_chunks)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def delete_document(self, filename):
        """Deletes a document and cascade deletes its chunks and FTS index."""
        filename = safe_filename(filename)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM documents WHERE filename = ?", (filename,))
            row = cursor.fetchone()
            if row:
                doc_id = row[0]
                # Delete FTS index first
                cursor.execute("""
                    DELETE FROM parent_chunks_fts 
                    WHERE rowid IN (SELECT id FROM parent_chunks WHERE document_id = ?)
                """, (doc_id,))
                
                # Delete document (cascade will clean up parent_chunks and child_chunks)
                cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                
                # Delete physical file from stored_documents
                stored_path = os.path.join("stored_documents", filename)
                if os.path.exists(stored_path):
                    os.remove(stored_path)

                conn.commit()
                self._invalidate_vector_cache()
                return True
            return False
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def get_all_documents(self):
        """Returns list of all uploaded global reference documents (claim_id is NULL)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT filename, file_type, file_size, uploaded_at FROM documents WHERE claim_id IS NULL ORDER BY uploaded_at DESC")
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

    def get_claim_documents(self, claim_id):
        """Returns list of all documents attached to a specific claim."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT filename, file_type, file_size, uploaded_at FROM documents WHERE claim_id = ? ORDER BY uploaded_at DESC", (claim_id,))
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

    def get_claim_chunks(self, claim_id):
        """Returns every parent chunk belonging to a claim's own documents, unranked.

        Claim dossiers are small (a handful of documents, 1-2 chunks each), so
        there's no need to compete them against global policy documents for a
        semantic-similarity slot -- a chunk specific to the active claim is
        always relevant context when that claim is the one being discussed.
        Semantic search scoped to a claim was found to sometimes rank a
        globally-similar policy document over the claim's own narrowly-worded
        receipt/report, silently dropping the one fact that actually answers
        a claim-specific question (multi-hop math queries especially).
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.content, d.filename, d.file_type
            FROM parent_chunks p
            JOIN documents d ON p.document_id = d.id
            WHERE d.claim_id = ?
            ORDER BY d.filename, p.chunk_index
        """, (claim_id,))
        rows = cursor.fetchall()
        conn.close()
        # score=1.0 is a sentinel meaning "guaranteed relevant by claim scope,"
        # not a real similarity score -- callers format/round this as a float,
        # so it must stay numeric, not None.
        return [
            {"content": content, "filename": filename, "file_type": file_type, "score": 1.0}
            for content, filename, file_type in rows
        ]

    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True):
        """Computes hybrid similarity (Vector + FTS5) with RRF and optional Cross-Encoder reranking scoped by claim_id.

        use_fts=False skips keyword search/RRF entirely and returns pure vector-only
        results, used by the eval harness to produce a naive baseline for comparison.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # --- 1. Vector Search (Child Chunks) Scoped by claim_id ---
        cache = self._vector_cache or self._build_vector_cache()
        matrix = cache["matrix"]

        vector_ranked = []
        if matrix.shape[0] > 0:
            # Mask to global docs plus (optionally) the active claim's docs.
            mask = cache["global_mask"]
            if claim_id is not None:
                mask = mask | (cache["claim_ids"] == claim_id)

            selected = np.where(mask)[0]
            if selected.size > 0:
                query = np.array(query_embedding, dtype=np.float32)
                query_norm = np.linalg.norm(query)
                query = query / (query_norm if query_norm != 0 else 1.0)

                # Both matrix rows and query are unit vectors → dot == cosine.
                similarities = matrix[selected] @ query

                contents = cache["contents"]
                filenames = cache["filenames"]
                file_types = cache["file_types"]
                parent_ids = cache["parent_ids"]

                # Map parent ID to maximum child score
                parent_best_scores = {}
                parent_metadata = {}
                for local_idx, global_idx in enumerate(selected):
                    p_id = parent_ids[global_idx]
                    score = float(similarities[local_idx])
                    if p_id not in parent_best_scores or score > parent_best_scores[p_id]:
                        parent_best_scores[p_id] = score
                        parent_metadata[p_id] = {
                            "content": contents[global_idx],
                            "filename": filenames[global_idx],
                            "file_type": file_types[global_idx]
                        }

                # Compile vector results
                for p_id, score in parent_best_scores.items():
                    meta = parent_metadata[p_id]
                    vector_ranked.append({
                        "id": p_id,
                        "content": meta["content"],
                        "filename": meta["filename"],
                        "file_type": meta["file_type"],
                        "score": score
                    })
                vector_ranked.sort(key=lambda x: x["score"], reverse=True)

        # --- 2. Keyword Search (FTS5 on Parent Chunks) Scoped by claim_id ---
        fts_ranked = []
        clean_query = " ".join([t for t in query_text.split() if t.isalnum()])
        if use_fts and clean_query:
            try:
                cursor.execute("""
                    SELECT p.content, d.filename, d.file_type, p.id
                    FROM parent_chunks p
                    JOIN documents d ON p.document_id = d.id
                    JOIN parent_chunks_fts f ON p.id = f.rowid
                    WHERE (d.claim_id IS NULL OR d.claim_id = ?)
                      AND parent_chunks_fts MATCH ?
                    LIMIT 40
                """, (claim_id, clean_query))
                fts_rows = cursor.fetchall()
                
                for content, filename, file_type, p_id in fts_rows:
                    fts_ranked.append({
                        "id": p_id,
                        "content": content,
                        "filename": filename,
                        "file_type": file_type,
                        "score": 0.0
                    })
            except sqlite3.OperationalError:
                pass
                
        conn.close()

        # --- 3. Reciprocal Rank Fusion (RRF) ---
        k_const = 60
        rrf_scores = {}
        parent_info = {}
        
        for rank, item in enumerate(vector_ranked):
            p_id = item["id"]
            rrf_scores[p_id] = rrf_scores.get(p_id, 0.0) + (1.0 / (k_const + rank + 1))
            parent_info[p_id] = item
            
        for rank, item in enumerate(fts_ranked):
            p_id = item["id"]
            rrf_scores[p_id] = rrf_scores.get(p_id, 0.0) + (1.0 / (k_const + rank + 1))
            parent_info[p_id] = item
            
        fused_results = []
        for p_id, score in rrf_scores.items():
            meta = parent_info[p_id]
            fused_results.append({
                "id": p_id,
                "content": meta["content"],
                "filename": meta["filename"],
                "file_type": meta["file_type"],
                "score": score
            })
            
        fused_results.sort(key=lambda x: x["score"], reverse=True)
        # Keep a candidate pool at least as large as the requested top_k so the
        # reranker (and non-reranked path) can actually return top_k results.
        top_candidates = fused_results[:max(15, top_k)]
        
        # --- 4. Cross-Encoder Reranking ---
        if reranking_engine and top_candidates:
            reranked = reranking_engine.rerank(query_text, top_candidates, top_k=top_k)
            for item in reranked:
                item["score"] = item["rerank_score"]
            return reranked
            
        return top_candidates[:top_k]
