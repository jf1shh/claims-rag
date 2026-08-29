import os
# Force offline-only execution for Hugging Face transformers/sentence-transformers
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import sqlite3
import time
from abc import ABC, abstractmethod
import numpy as np
import pypdf
import docx
import pandas as pd
# NOTE: sentence_transformers/torch are imported lazily inside EmbeddingEngine
# and RerankingEngine so that the pure-numpy SQLiteVectorStore (and document
# parsing) can be imported and exercised without loading the heavy ML stack.

# ---------------------------------------------------------------------------
# Storage paths. Anchored to the repository root so the server behaves the
# same regardless of the working directory it is started from (a CWD-relative
# default silently created a fresh, empty rag_store.db when uvicorn was
# launched elsewhere). Both are overridable via env vars -- Phase 1 of the
# enterprise migration (docs/enterprise-migration.md) replaces this SQLite
# backend behind the VectorStore interface.
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("RAG_DB_PATH") or os.path.join(REPO_ROOT, "rag_store.db")
STORED_DOCUMENTS_DIR = os.environ.get("STORED_DOCUMENTS_DIR") or os.path.join(REPO_ROOT, "stored_documents")


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


def _blob_key(claim_id: str | None, filename: str) -> str:
    """Builds the object-store key for a source document. The scope (claim id,
    or ``global`` for the global set) is part of the key so one bucket never
    mixes a claim's and the global corpus's copy of the same filename. The
    tenant prefix itself lives in the blob-store adapter.
    """
    scope = claim_id if claim_id else "global"
    return f"{scope}/{filename}"


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
        for page in reader.pages:
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
            # The cross-encoder emits raw logits (unbounded, often >1), but
            # every consumer treats scores as 0-1 relevance (the UI renders
            # them as percentages -- raw logits displayed as "386% similarity").
            # Sigmoid squashes to 0-1 and is monotonic, so ranking order is
            # unchanged everywhere.
            passages[idx]["rerank_score"] = float(1.0 / (1.0 + np.exp(-score)))

        passages.sort(key=lambda x: x["rerank_score"], reverse=True)
        return passages[:top_k]


class VectorStore(ABC):
    """Contract every storage backend must implement.

    Callers (backend/app.py, backend/agentic_router.py, ingest_all.py, the
    eval harness, and the parity runner) depend only on this interface, so a
    backend can be swapped without touching them. SQLiteVectorStore is the
    current implementation; PostgresVectorStore (pgvector + Postgres FTS,
    tenant scoping) lands in Phase 1 of docs/enterprise-migration.md.

    File-backed backends copy source documents to a storage directory; the
    DB keeps the metadata + chunks + embeddings, and the two stay consistent
    (writes to disk happen only after a successful DB commit).
    """

    @abstractmethod
    def add_document(self, filename, file_type, file_size, text, embedding_engine, claim_id=None, file_path=None):
        """Indexes a document (chunk + embed + physical copy) and returns (doc_id, parent_chunk_count)."""

    @abstractmethod
    def delete_document(self, filename) -> bool:
        """Removes a document and its chunks/embeddings/FTS rows; False if absent."""

    @abstractmethod
    def get_all_documents(self):
        """Global (claim_id IS NULL) documents, newest first."""

    @abstractmethod
    def get_claim_documents(self, claim_id):
        """Documents attached to one claim, newest first."""

    @abstractmethod
    def get_claim_chunks(self, claim_id):
        """Every parent chunk of a claim's own documents, unranked (score=1.0 sentinel)."""

    @abstractmethod
    def get_document_content(self, filename) -> str:
        """Reconstructs a document's full text by joining its parent chunks in order."""

    @abstractmethod
    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True):
        """Hybrid retrieval (vector + keyword + RRF), optionally reranked."""


class SQLiteVectorStore(VectorStore):
    def __init__(self, db_path=DB_PATH, storage_dir=STORED_DOCUMENTS_DIR, blob_store=None):
        self.db_path = db_path
        # Directory for physical copies of source documents. Defaults to the
        # repo-root-anchored stored_documents/; tests pass an explicit temp
        # dir to stay hermetic.
        self.storage_dir = storage_dir
        # Optional object-storage adapter (Phase 2, S3). When set, add/delete
        # route source bytes through it instead of self.storage_dir, and the
        # serving path uses its create_download_url. When None, the legacy
        # filesystem behavior below is unchanged.
        self.blob_store = blob_store
        # In-memory, pre-normalized embedding index. Built lazily on first
        # search and invalidated whenever documents are added/removed. This
        # avoids re-reading every embedding BLOB and recomputing corpus norms
        # on each query (the primary CPU bottleneck as the corpus scales).
        self._vector_cache = None
        self._init_db()

    def _connect(self):
        """Opens a connection with foreign-key enforcement enabled. SQLite
        ships with PRAGMA foreign_keys OFF per-connection, so the schema's ON
        DELETE CASCADE clauses silently never fire on a raw connect() --
        deleting a document orphaned its parent/child chunk rows instead of
        cascading (verified empirically). Every connection in this class must
        go through here."""
        conn = sqlite3.connect(self.db_path)  # sole raw connect in this class
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _invalidate_vector_cache(self):
        self._vector_cache = None

    def _build_vector_cache(self):
        """Loads all child embeddings once, L2-normalizes them, and caches the
        matrix plus parallel metadata arrays for fast masked cosine search."""
        conn = self._connect()
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
        conn = self._connect()
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
        """Inserts document, parent chunks, FTS index, child chunks and their embeddings; copies physical file to disk.

        Physical-file writes are deferred until after the DB transaction commits
        successfully -- previously the old file was deleted (and, on the
        no-file_path fallback path, the new text was written) *inside* the same
        try block as the chunk/embedding inserts. If anything failed after that
        point (e.g. embedding generation raised), conn.rollback() restored the
        DB to describe the OLD document, but the physical file on disk had
        already been overwritten with the NEW (failed, partial) content --
        permanently desyncing what's indexed/searchable from what a handler
        sees when they open the file. Verified empirically with a mid-overwrite
        embedding failure. Deferring the file write also fixes a second latent
        bug: the old fallback-text-write path only wrote `if not
        os.path.exists(dest_path)`, so overwriting a document added without a
        file_path (no physical source) silently kept serving the OLD file
        content forever while the DB/search index moved on to the NEW text.
        """
        import shutil
        filename = safe_filename(filename)
        os.makedirs(self.storage_dir, exist_ok=True)

        conn = self._connect()
        cursor = conn.cursor()

        try:
            # Delete if exists (to overwrite). Scope guard: the documents table
            # keys on filename alone, so an overwrite must only be allowed when
            # the existing row belongs to the SAME scope (same claim, or both
            # global). Otherwise uploading "report.pdf" to claim B would
            # silently delete claim A's "report.pdf" -- cross-claim data loss
            # (verified empirically: the row, its chunks, and the physical
            # file are all replaced).
            cursor.execute("SELECT id, claim_id FROM documents WHERE filename = ?", (filename,))
            existing = cursor.fetchone()
            if existing:
                existing_claim = existing[1]
                if existing_claim != claim_id:
                    existing_scope = f"claim {existing_claim}" if existing_claim else "global guidelines"
                    new_scope = f"claim {claim_id}" if claim_id else "global guidelines"
                    raise ValueError(
                        f"'{filename}' already exists in {existing_scope}; refusing to overwrite it "
                        f"from {new_scope}. Rename the uploaded file (or delete the existing one) first "
                        "-- claim folders and global guidelines must not overwrite each other."
                    )
                doc_id = existing[0]
                # Delete FTS index
                cursor.execute("DELETE FROM parent_chunks_fts WHERE rowid IN (SELECT id FROM parent_chunks WHERE document_id = ?)", (doc_id,))
                # Delete document (physical file is handled after commit, below)
                cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            # Insert document
            uploaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO documents (filename, file_type, file_size, uploaded_at, claim_id) VALUES (?, ?, ?, ?, ?)",
                (filename, file_type, file_size, uploaded_at, claim_id)
            )
            doc_id = cursor.lastrowid

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
                for c_text, embedding in zip(child_chunks, embeddings, strict=True):
                    emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
                    cursor.execute(
                        "INSERT INTO child_chunks (parent_id, content, embedding) VALUES (?, ?, ?)",
                        (p_id, c_text, emb_bytes)
                    )

            conn.commit()
            self._invalidate_vector_cache()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        # Only touch storage once the DB write has durably committed -- see
        # the docstring above for why this ordering matters (a rolled-back DB
        # write must never pair with a storage-side change). When an object
        # store is configured the source bytes go to it (tenant-scoped key);
        # otherwise the legacy filesystem path writes atomically to
        # storage_dir.
        if self.blob_store is not None:
            key = _blob_key(claim_id, filename)
            if file_path and os.path.exists(file_path):
                with open(file_path, "rb") as fh:
                    content = fh.read()
            else:
                content = text.encode("utf-8", errors="ignore")
            self.blob_store.put(key, content, file_type or "application/octet-stream")
        else:
            dest_path = os.path.abspath(os.path.join(self.storage_dir, filename))
            tmp_path = dest_path + ".part"
            if file_path and os.path.exists(file_path):
                src_abs = os.path.abspath(file_path)
                if src_abs != dest_path:
                    shutil.copy2(file_path, tmp_path)
                    os.replace(tmp_path, dest_path)
            else:
                with open(tmp_path, "w", encoding="utf-8", errors="ignore") as f:
                    f.write(text)
                os.replace(tmp_path, dest_path)

        return doc_id, len(parent_chunks)

    def delete_document(self, filename):
        """Deletes a document and cascade deletes its chunks and FTS index.

        The physical file is removed only after the DB delete has durably
        committed -- the mirror image of the add_document ordering fix (see
        its docstring): a failed/rolled-back DB delete must never leave the
        physical file gone while the document is still indexed and
        searchable.
        """
        filename = safe_filename(filename)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, claim_id FROM documents WHERE filename = ?", (filename,))
            row = cursor.fetchone()
            if not row:
                return False
            doc_id, claim_of_deleted = row[0], row[1]
            # Delete FTS index first
            cursor.execute("""
                DELETE FROM parent_chunks_fts
                WHERE rowid IN (SELECT id FROM parent_chunks WHERE document_id = ?)
            """, (doc_id,))

            # Delete document (cascade will clean up parent_chunks and child_chunks)
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            conn.commit()
            self._invalidate_vector_cache()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        # Delete from storage only now that the DB is guaranteed to no longer
        # reference it. With an object store, the key needs the scope (claim)
        # that was just deleted, so look it up before the delete.
        if self.blob_store is not None and claim_of_deleted:
            self.blob_store.delete(_blob_key(claim_of_deleted, filename))
        elif not self.blob_store:
            stored_path = os.path.join(self.storage_dir, filename)
            if os.path.exists(stored_path):
                os.remove(stored_path)
        return True

    def get_all_documents(self):
        """Returns list of all uploaded global reference documents (claim_id is NULL)."""
        conn = self._connect()
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
        conn = self._connect()
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
        conn = self._connect()
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

    def get_document_content(self, filename):
        """Reconstructs a document's full text by joining its parent chunks in
        order. Backend-neutral replacement for app.py's old inline sqlite read
        (which could not work on a Postgres backend)."""
        conn = self._connect()
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
        return "\n\n".join(r[0] for r in rows)

    def get_blob_key(self, filename):
        """Resolves the object-store key for a document by its filename (used
        by the serving path to presign its download URL). Returns None if the
        document is not indexed. Note: filename is unique here (the SQLite
        schema keys the documents table on filename alone), so the scope is
        whatever row owns that name."""
        filename = safe_filename(filename)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT claim_id FROM documents WHERE filename = ?", (filename,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return _blob_key(row[0], filename)

    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True):
        """Computes hybrid similarity (Vector + FTS5) with RRF and optional Cross-Encoder reranking scoped by claim_id.

        use_fts=False skips keyword search/RRF entirely and returns pure vector-only
        results, used by the eval harness to produce a naive baseline for comparison.
        """
        conn = self._connect()
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
        # Quote every token: FTS5 reserved words (AND, OR, NOT, NEAR, etc.)
        # passed bare are parsed as operators, which raises a syntax error the
        # except below swallows -- silently dropping the entire keyword leg
        # for queries like "deductible or not covered". Quoting forces each
        # token to be a literal term, preserving the implicit-AND semantics.
        tokens = [t for t in query_text.split() if t.isalnum()]
        clean_query = " ".join(f'"{t}"' for t in tokens)
        if use_fts and clean_query:
            try:
                # ORDER BY rank (bm25) is load-bearing: RRF below scores each
                # list by rank position, and without it FTS5 returns rows in
                # rowid (insertion) order -- arbitrary ranks for fusion, and
                # LIMIT truncating by age rather than relevance.
                cursor.execute("""
                    SELECT p.content, d.filename, d.file_type, p.id
                    FROM parent_chunks p
                    JOIN documents d ON p.document_id = d.id
                    JOIN parent_chunks_fts f ON p.id = f.rowid
                    WHERE (d.claim_id IS NULL OR d.claim_id = ?)
                      AND parent_chunks_fts MATCH ?
                    ORDER BY rank
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
