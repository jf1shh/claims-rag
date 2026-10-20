import os
# Force offline-only execution for Hugging Face transformers/sentence-transformers
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import logging
import re
import sqlite3
import threading
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
logger = logging.getLogger(__name__)

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


def resolve_rerank_device(preference="auto"):
    """Maps a RERANK_DEVICE preference onto a device torch can actually use.

    "auto" picks the GPU when one is present and falls back to CPU otherwise,
    so the same default works on a ROCm/CUDA box and in CI. An explicit "cuda"
    on a machine without a visible GPU falls back too (with a warning) rather
    than crashing the whole app at startup over a reranker. Note that ROCm
    builds of torch expose AMD cards through the "cuda" device name -- there is
    no separate "rocm" device.
    """
    if preference == "cpu":
        return "cpu"

    import torch

    if torch.cuda.is_available():
        return "cuda"
    if preference == "cuda":
        print("RERANK_DEVICE=cuda requested but no GPU is visible to torch; using CPU.")
    return "cpu"


class RerankingEngine:
    def __init__(self, model_name="cross-encoder/ms-marco-MiniLM-L-6-v2", device="auto"):
        from sentence_transformers import CrossEncoder
        resolved = resolve_rerank_device(device)
        print(f"Loading reranking model '{model_name}' on {resolved}...")
        self.model = CrossEncoder(model_name, device=resolved)
        self.device = resolved
        print("Reranking model loaded successfully.")

    def score_pairs(self, pairs, inference_batch_size=16):
        """Return one raw cross-encoder score per pair, in input order."""
        if not pairs:
            return []
        scores = self.model.predict(
            pairs,
            show_progress_bar=False,
            batch_size=inference_batch_size,
        )
        return [float(score) for score in scores]

    def rerank(self, query, passages, top_k=4):
        """Scores passages against query and returns top_k sorted copies."""
        if not passages:
            return []

        pairs = [(query, p["content"]) for p in passages]
        scores = self.score_pairs(pairs)
        scored_passages = []
        for passage, score in zip(passages, scores, strict=True):
            # The cross-encoder emits raw logits (unbounded, often >1), but
            # every consumer treats scores as 0-1 relevance (the UI renders
            # them as percentages -- raw logits displayed as "386% similarity").
            # Sigmoid squashes to 0-1 and is monotonic, so ranking order is
            # unchanged everywhere.
            scored_passage = dict(passage)
            scored_passage["rerank_score"] = float(1.0 / (1.0 + np.exp(-score)))
            scored_passages.append(scored_passage)

        scored_passages.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored_passages[:top_k]


_PIN_TYPE_WORDS = {"sop", "guideline", "guide", "rider", "endorsement", "directive"}
_PIN_GENERIC_WORDS = {"auto", "claim", "vehicle", "adjuster", "insurance", "policy", "plan", "form", "plus"}


def _pin_singularize(token):
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _pin_is_droppable(token):
    """Purely numeric tokens ("2026") and letters+digits ("form402") carry no title signal."""
    return bool(re.fullmatch(r"[0-9]+", token) or re.fullmatch(r"[a-z]+[0-9]+", token))


def _pin_tokenize_query(text):
    return [_pin_singularize(t) for t in re.findall(r"[a-z0-9]+", text.lower())]


def _pin_title_tokens(filename):
    """Filename stem, split on separators and camelCase boundaries, lowercased,
    singularized, with purely numeric/alnum-suffix tokens dropped."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    tokens = []
    for part in re.split(r"[_\-\s]+", stem):
        if not part:
            continue
        token = _pin_singularize(part.lower())
        if _pin_is_droppable(token):
            continue
        tokens.append(token)
    return tokens


def _pin_is_named(doc, query_tokens):
    """True if `query_tokens` names `doc` by document type plus distinctive title words."""
    if doc.get("claim_id") is not None:
        return False
    tokens = _pin_title_tokens(doc["filename"])
    type_words = [t for t in tokens if t in _PIN_TYPE_WORDS]
    if not type_words or not any(t in query_tokens for t in type_words):
        return False
    distinctive = list(dict.fromkeys(t for t in tokens if t not in _PIN_TYPE_WORDS and t not in _PIN_GENERIC_WORDS))
    if not distinctive:
        return False
    hits = sum(1 for t in distinctive if t in query_tokens)
    return hits * 2 >= len(distinctive)


def _pin_doc_key(doc):
    return (doc.get("claim_id"), doc["filename"])


def pin_named_documents(query_text, ranked, top_k, max_pins=2):
    """Pin global reference documents (SOPs/guidelines/etc.) that the query names
    explicitly by type + distinctive title words into the final top_k, even if the
    cross-encoder ranked them lower.

    A document is only eligible if it is global (`claim_id is None`); claim
    documents are never pinned. Only documents already present in `ranked` --
    which the caller has already filtered to the requesting scope -- are ever
    considered, and no other lookup is performed, so this cannot leak another
    claim's or tenant's documents. Pinned copies are marked
    `"pinned": "named_document"`; the input list and its dicts are not mutated.
    Output stays sorted by each item's original position in `ranked`, so scores
    remain monotone non-increasing.
    """
    n = len(ranked)
    if top_k >= n or top_k <= 0:
        return ranked[:top_k]

    query_tokens = set(_pin_tokenize_query(query_text))

    # (position in `ranked`, item, is_pin)
    selection = [[i, ranked[i], False] for i in range(top_k)]
    # Keyed by document identity (claim_id, filename): a claim file named like a
    # global SOP must not stop the SOP from being pinned.
    selected_docs = {_pin_doc_key(entry[1]) for entry in selection}

    pins_done = 0
    processed_docs = set()
    for idx, doc in enumerate(ranked):
        if pins_done >= max_pins:
            break
        key = _pin_doc_key(doc)
        if key in processed_docs:
            continue
        processed_docs.add(key)
        if key in selected_docs:
            continue
        if not _pin_is_named(doc, query_tokens):
            continue

        replaceable = [i for i, entry in enumerate(selection) if not entry[2]]
        if not replaceable:
            break
        replace_at = max(replaceable, key=lambda i: selection[i][0])

        selected_docs.discard(_pin_doc_key(selection[replace_at][1]))
        pinned_doc = dict(doc)
        pinned_doc["pinned"] = "named_document"
        selection[replace_at] = [idx, pinned_doc, True]
        selected_docs.add(key)
        pins_done += 1

    selection.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in selection]


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
    def delete_document(self, filename, claim_id=None) -> bool:
        """Removes a document scoped to (claim_id, filename); False if absent."""

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
    def get_document_content(self, filename, claim_id=None) -> str:
        """Reconstructs a document's full text by joining its parent chunks in order."""

    @abstractmethod
    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True, candidate_pool=50):
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
        # Guards _vector_cache and _vector_cache_generation. A build reads
        # rows without holding the lock (that's the whole point of the
        # cache), so an invalidation can land mid-build; the generation
        # counter lets the build detect that race and discard its result
        # instead of installing a matrix that's already stale.
        self._vector_cache_lock = threading.Lock()
        self._vector_cache_generation = 0
        self._init_db()

    def _connect(self):
        """Opens a connection with foreign-key enforcement enabled. SQLite
        ships with PRAGMA foreign_keys OFF per-connection, so the schema's ON
        DELETE CASCADE clauses silently never fire on a raw connect() --
        deleting a document orphaned its parent/child chunk rows instead of
        cascading (verified empirically). Every connection in this class must
        go through here. timeout=30 covers the busy wait for the now-short
        write window in add_document/delete_document (chunking/embedding no
        longer happens while the write transaction is open, but 30s gives
        concurrent writers slack instead of the 5s default)."""
        conn = sqlite3.connect(self.db_path, timeout=30)  # sole raw connect in this class
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _invalidate_vector_cache(self):
        with self._vector_cache_lock:
            self._vector_cache_generation += 1
            self._vector_cache = None

    def _build_vector_cache(self):
        """Loads all child embeddings once, L2-normalizes them, and caches the
        matrix plus parallel metadata arrays for fast masked cosine search.

        Reads the database without holding the lock, so an add/delete can
        invalidate the cache while this build is in flight. The generation
        check below detects that and discards the now-stale result instead
        of installing it -- otherwise a build racing an invalidation could
        overwrite the None with a matrix missing the new document or still
        containing a deleted one, and that staleness would persist until the
        next write. The built result is still returned to the caller either
        way: this one search is entitled to use it even if it won't be cached.
        """
        with self._vector_cache_lock:
            generation = self._vector_cache_generation

        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.embedding, p.content, d.filename, d.file_type, p.id, d.claim_id, d.id, d.document_version
            FROM child_chunks c
            JOIN parent_chunks p ON c.parent_id = p.id
            JOIN documents d ON p.document_id = d.id
        """)
        rows = cursor.fetchall()
        conn.close()

        vectors, contents, filenames, file_types, parent_ids, claim_ids = [], [], [], [], [], []
        doc_ids, versions = [], []
        for emb_bytes, content, filename, file_type, p_id, claim_id, doc_id, version in rows:
            vec = np.frombuffer(emb_bytes, dtype=np.float32)
            if vec.shape[0] != 384:
                continue
            vectors.append(vec)
            contents.append(content)
            filenames.append(filename)
            file_types.append(file_type)
            parent_ids.append(p_id)
            claim_ids.append(claim_id)
            doc_ids.append(str(doc_id))
            versions.append(version or "legacy-unversioned")

        if vectors:
            matrix = np.vstack(vectors).astype(np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            matrix = matrix / norms  # pre-normalized so cosine == dot product
        else:
            matrix = np.zeros((0, 384), dtype=np.float32)

        built = {
            "matrix": matrix,
            "contents": contents,
            "filenames": filenames,
            "file_types": file_types,
            "parent_ids": parent_ids,
            "document_ids": doc_ids, "document_versions": versions,
            "claim_ids": np.array(claim_ids, dtype=object),
            "global_mask": np.array([cid is None for cid in claim_ids], dtype=bool),
        }

        with self._vector_cache_lock:
            if self._vector_cache_generation == generation:
                self._vector_cache = built
        return built

    @staticmethod
    def _delete_orphan_chunks(cursor, existing):
        """Delete chunk/FTS rows whose document no longer exists (migration helper)."""
        orphan_parents = "SELECT id FROM parent_chunks WHERE document_id NOT IN (SELECT id FROM documents)"
        if "child_chunks" in existing:
            cursor.execute(f"DELETE FROM child_chunks WHERE parent_id IN ({orphan_parents})")
        if "parent_chunks_fts" in existing:
            cursor.execute(f"DELETE FROM parent_chunks_fts WHERE rowid IN ({orphan_parents})")
        cursor.execute("DELETE FROM parent_chunks WHERE document_id NOT IN (SELECT id FROM documents)")

    def _init_db(self):
        conn = self._connect()
        cursor = conn.cursor()

        # Documents table (with claim_id tag). No column-level UNIQUE on
        # filename -- identity is (claim_id, filename), enforced below by
        # ix_documents_scope so the same filename can exist once per scope
        # (global = claim_id IS NULL).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                file_type TEXT,
                file_size INTEGER,
                uploaded_at TEXT,
                claim_id TEXT
            )
        """)

        columns = {row[1] for row in cursor.execute("PRAGMA table_info(documents)")}
        for column in ("storage_key", "document_version"):
            if column not in columns:
                cursor.execute(f"ALTER TABLE documents ADD COLUMN {column} TEXT")

        # Legacy DBs were created with `filename TEXT UNIQUE` (global filename
        # identity). sqlite_master still carries that literal DDL for them --
        # ALTER TABLE ADD COLUMN above doesn't rewrite it -- so detect it and
        # rebuild the table without the column-level UNIQUE, preserving ids
        # (parent_chunks.document_id references them) and every other column.
        # Idempotent: a migrated DB's DDL no longer contains the marker.
        table_sql = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()
        if table_sql and table_sql[0] and "filename TEXT UNIQUE" in table_sql[0]:
            # Build the replacement under a fresh name, drop the old table,
            # then rename the replacement into place -- never rename the live
            # `documents` table away first. SQLite's ALTER TABLE RENAME
            # rewrites *other* tables' FK references to the renamed table by
            # default, so renaming `documents` to `documents_old` first would
            # silently repoint parent_chunks.document_id's FK at
            # `documents_old`, breaking it the moment that table is dropped.
            #
            # PRAGMA foreign_keys is a no-op inside a transaction, so it must
            # be set before BEGIN. Everything from here to COMMIT runs as one
            # transaction: CREATE TABLE autocommits on its own otherwise, so a
            # failure partway through (e.g. the INSERT) used to leave an empty
            # `documents_new` behind and every subsequent restart failed with
            # "table documents_new already exists".
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("BEGIN")
            try:
                # A previous migration attempt may have failed after creating
                # documents_new but before the rename -- drop it so CREATE
                # TABLE below doesn't fail on "already exists".
                cursor.execute("DROP TABLE IF EXISTS documents_new")
                old_seq_row = cursor.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'documents'"
                ).fetchone()
                old_seq = old_seq_row[0] if old_seq_row else 0
                cursor.execute("""
                    CREATE TABLE documents_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT,
                        file_type TEXT,
                        file_size INTEGER,
                        uploaded_at TEXT,
                        claim_id TEXT,
                        storage_key TEXT,
                        document_version TEXT
                    )
                """)
                cursor.execute("""
                    INSERT INTO documents_new (id, filename, file_type, file_size, uploaded_at, claim_id, storage_key, document_version)
                    SELECT id, filename, file_type, file_size, uploaded_at, claim_id, storage_key, document_version FROM documents
                """)
                cursor.execute("DROP TABLE documents")
                cursor.execute("ALTER TABLE documents_new RENAME TO documents")

                # The rebuild above re-created `documents` as a fresh
                # AUTOINCREMENT table, which loses the old high-water mark.
                # Without restoring it, a previously-deleted id gets reused by
                # the next upload and that new document silently inherits any
                # orphaned chunk rows still carrying the old document_id.
                max_id_row = cursor.execute("SELECT MAX(id) FROM documents").fetchone()
                max_id = max_id_row[0] or 0
                new_seq = max(old_seq, max_id)
                updated = cursor.execute(
                    "UPDATE sqlite_sequence SET seq = ? WHERE name = 'documents'", (new_seq,)
                ).rowcount
                if not updated:
                    cursor.execute(
                        "INSERT INTO sqlite_sequence (name, seq) VALUES ('documents', ?)", (new_seq,)
                    )

                # Orphan parent/child/FTS rows (document_id pointing at an id
                # that no longer exists) predate this migration and were never
                # cascade-deleted; left in place they can attach to a future
                # upload that reuses the id. The chunk tables are created
                # further down, so a DB that predates them (2026-07-16) has
                # nothing to clean -- skip rather than fail on "no such table".
                existing = {
                    row[0] for row in cursor.execute(
                        "SELECT name FROM sqlite_master WHERE name IN "
                        "('parent_chunks', 'child_chunks', 'parent_chunks_fts')"
                    )
                }
                if "parent_chunks" in existing:
                    self._delete_orphan_chunks(cursor, existing)

                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                cursor.execute("PRAGMA foreign_keys = ON")

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_scope
            ON documents (filename, COALESCE(claim_id, ''))
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
        """Stage immutable source bytes, then publish their reference with the index.

        A failed stage or transaction leaves the previous indexed version intact.
        Unreferenced staged blobs can be reclaimed by the maintenance command.

        Chunking and embedding run BEFORE any write statement -- embedding a
        large document can take seconds, and running it inside the write
        transaction held SQLite's write lock that whole time, so any
        concurrent upload/delete hit "database is locked" against the default
        busy timeout. The write transaction below only covers the scope
        check and the inserts themselves.
        """
        from backend.source_storage import stage_source
        filename = safe_filename(filename)
        os.makedirs(self.storage_dir, exist_ok=True)

        # 1. Generate parent/child chunks and embeddings up front, with no
        # transaction open.
        parent_chunks = TextChunker.chunk(text, chunk_size=1200, chunk_overlap=200)
        parent_payloads = []
        for p_idx, p_text in enumerate(parent_chunks):
            child_chunks = TextChunker.chunk(p_text, chunk_size=250, chunk_overlap=50)
            embeddings = embedding_engine.embed_chunks(child_chunks) if child_chunks else []
            child_payloads = [
                (c_text, np.array(embedding, dtype=np.float32).tobytes())
                for c_text, embedding in zip(child_chunks, embeddings, strict=True)
            ]
            parent_payloads.append((p_idx, p_text, child_payloads))

        conn = self._connect()
        cursor = conn.cursor()

        try:
            # Overwrite only the existing row in the SAME scope (identity is
            # (claim_id, filename); a NULL claim_id is the global scope). Must
            # run against current DB state inside this transaction, i.e. after
            # embedding above.
            cursor.execute("SELECT id FROM documents WHERE filename = ? AND claim_id IS ?", (filename, claim_id))
            existing = cursor.fetchone()
            if existing:
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

            # 2. Insert the already-computed parent/child chunks and embeddings.
            for p_idx, p_text, child_payloads in parent_payloads:
                cursor.execute(
                    "INSERT INTO parent_chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                    (doc_id, p_idx, p_text)
                )
                p_id = cursor.lastrowid

                cursor.execute(
                    "INSERT INTO parent_chunks_fts (rowid, content) VALUES (?, ?)",
                    (p_id, p_text)
                )

                for c_text, emb_bytes in child_payloads:
                    cursor.execute(
                        "INSERT INTO child_chunks (parent_id, content, embedding) VALUES (?, ?, ?)",
                        (p_id, c_text, emb_bytes)
                    )

            storage_key, document_version = stage_source(self, filename, file_type, text, file_path)
            cursor.execute("UPDATE documents SET storage_key = ?, document_version = ? WHERE id = ?",
                           (storage_key, document_version, doc_id))
            conn.commit()
            self._invalidate_vector_cache()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        return doc_id, len(parent_chunks)

    def delete_document(self, filename, claim_id=None):
        """Deletes the document in the given scope (claim_id, filename) and
        cascade deletes its chunks and FTS index.

        The physical file is removed only after the DB delete has durably
        committed -- the mirror image of the add_document ordering fix (see
        its docstring): a failed/rolled-back DB delete must never leave the
        physical file gone while the document is still indexed and
        searchable.
        """
        filename = safe_filename(filename)
        source_key = self.get_blob_key(filename, claim_id)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, storage_key FROM documents WHERE filename = ? AND claim_id IS ?", (filename, claim_id))
            row = cursor.fetchone()
            if not row:
                return False
            doc_id, storage_key = row
            # Delete FTS index first
            cursor.execute("""
                DELETE FROM parent_chunks_fts
                WHERE rowid IN (SELECT id FROM parent_chunks WHERE document_id = ?)
            """, (doc_id,))

            # Delete document (cascade will clean up parent_chunks and child_chunks)
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            # Legacy (pre-scope) rows have no storage_key and share a plain
            # storage_dir/<filename> physical file by name -- before per-scope
            # identity, filename was globally unique so at most one row ever
            # owned it. New uploads always get a scoped (versioned)
            # storage_key, so only another surviving legacy row can still
            # reference that same plain file.
            keep_legacy_file = False
            if storage_key is None:
                cursor.execute(
                    "SELECT 1 FROM documents WHERE filename = ? AND storage_key IS NULL LIMIT 1",
                    (filename,),
                )
                keep_legacy_file = cursor.fetchone() is not None

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
        if source_key:
            from backend.source_storage import source_store
            if self.blob_store is not None or source_key.startswith("versions/"):
                source_store(self).delete(source_key)
            elif not keep_legacy_file:
                source_store(self).delete(filename)
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
            SELECT p.content, d.filename, d.file_type, p.id, d.id, d.document_version
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
            {"content": content, "filename": filename, "file_type": file_type, "score": 1.0,
             "id": pid, "document_id": str(did), "document_version": version or "legacy-unversioned",
             "claim_id": claim_id}
            for content, filename, file_type, pid, did, version in rows
        ]

    def get_document_content(self, filename, claim_id=None):
        """Reconstructs a document's full text by joining its parent chunks in
        order. Backend-neutral replacement for app.py's old inline sqlite read
        (which could not work on a Postgres backend)."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.content
            FROM parent_chunks p
            JOIN documents d ON p.document_id = d.id
            WHERE d.filename = ? AND d.claim_id IS ?
            ORDER BY p.chunk_index ASC
        """, (filename, claim_id))
        rows = cursor.fetchall()
        conn.close()
        return "\n\n".join(r[0] for r in rows)

    def get_document_metadata(self, filename, claim_id=None):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, claim_id, filename, storage_key, document_version FROM documents WHERE filename = ? AND claim_id IS ?",
                (safe_filename(filename), claim_id),
            ).fetchone()
            return {"document_id": str(row[0]), "claim_id": row[1], "filename": row[2], "storage_key": row[3], "document_version": row[4] or "legacy-unversioned"} if row else None
        finally:
            conn.close()

    def get_blob_key(self, filename, claim_id=None):
        from backend.rag_engine import _blob_key
        metadata = self.get_document_metadata(filename, claim_id)
        if metadata is None:
            return None
        return metadata["storage_key"] or _blob_key(metadata["claim_id"], filename)


    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True, candidate_pool=50):
        """Computes hybrid similarity (Vector + FTS5) with RRF and optional Cross-Encoder reranking scoped by claim_id.

        use_fts=False skips keyword search/RRF entirely and returns pure vector-only
        results, used by the eval harness to produce a naive baseline for comparison.
        """
        conn = self._connect()
        cursor = conn.cursor()

        # --- 1. Vector Search (Child Chunks) Scoped by claim_id ---
        with self._vector_cache_lock:
            cache = self._vector_cache
        cache = cache or self._build_vector_cache()
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
                            "file_type": file_types[global_idx],
                            "document_id": cache["document_ids"][global_idx],
                            "document_version": cache["document_versions"][global_idx],
                            "claim_id": cache["claim_ids"][global_idx]
                        }

                # Compile vector results
                for p_id, score in parent_best_scores.items():
                    meta = parent_metadata[p_id]
                    vector_ranked.append({
                        **meta,
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
                    SELECT p.content, d.filename, d.file_type, p.id, d.id, d.document_version, d.claim_id
                    FROM parent_chunks p
                    JOIN documents d ON p.document_id = d.id
                    JOIN parent_chunks_fts f ON p.id = f.rowid
                    WHERE (d.claim_id IS NULL OR d.claim_id = ?)
                      AND parent_chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT 40
                """, (claim_id, clean_query))
                fts_rows = cursor.fetchall()

                for content, filename, file_type, p_id, doc_id, version, fts_claim_id in fts_rows:
                    fts_ranked.append({
                        "id": p_id, "document_id": str(doc_id), "document_version": version or "legacy-unversioned",
                        "content": content,
                        "filename": filename,
                        "file_type": file_type,
                        "claim_id": fts_claim_id,
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
                **meta,
                "id": p_id,
                "content": meta["content"],
                "filename": meta["filename"],
                "file_type": meta["file_type"],
                "score": score
            })

        fused_results.sort(key=lambda x: x["score"], reverse=True)
        # Bound reranking work by the configured candidate pool.
        top_candidates = fused_results[:candidate_pool]

        # --- 4. Cross-Encoder Reranking ---
        if reranking_engine and top_candidates:
            reranked = reranking_engine.rerank(query_text, top_candidates, top_k=len(top_candidates))
            for item in reranked:
                item["score"] = item["rerank_score"]
            if not use_fts:
                return reranked[:top_k]
            return pin_named_documents(query_text, reranked, top_k)

        if not use_fts:
            return top_candidates[:top_k]
        return pin_named_documents(query_text, top_candidates, top_k)
