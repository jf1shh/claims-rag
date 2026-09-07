"""Unit tests for backend/rag_engine.py -- chunking, filename sanitization,
and the SQLite vector store's write/delete/search invariants.

Deliberately torch-free: EmbeddingEngine/RerankingEngine import
sentence-transformers lazily, so these tests exercise the store with a
deterministic fake embedder and never load the ML stack (keeps CI fast and
runnable on any runner).
"""
import os
import sqlite3

import numpy as np
import pytest

from backend.rag_engine import SQLiteVectorStore, TextChunker, resolve_rerank_device, safe_filename


# ---------------------------------------------------------------------------
# resolve_rerank_device -- RERANK_DEVICE preference -> device torch can use.
# Stays torch-free per this module's contract: a stub module stands in, which
# also lets the no-GPU branch be exercised on a GPU machine and vice versa.
# ---------------------------------------------------------------------------

class TestResolveRerankDevice:
    @staticmethod
    def _stub_torch(monkeypatch, available):
        import sys
        import types

        stub = types.ModuleType("torch")
        stub.cuda = types.SimpleNamespace(is_available=lambda: available)
        monkeypatch.setitem(sys.modules, "torch", stub)

    def test_cpu_preference_never_touches_torch(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "torch", None)  # importing it would raise
        assert resolve_rerank_device("cpu") == "cpu"

    def test_auto_uses_gpu_when_visible(self, monkeypatch):
        self._stub_torch(monkeypatch, available=True)
        assert resolve_rerank_device("auto") == "cuda"

    def test_auto_falls_back_to_cpu_without_gpu(self, monkeypatch):
        self._stub_torch(monkeypatch, available=False)
        assert resolve_rerank_device("auto") == "cpu"

    def test_explicit_cuda_falls_back_rather_than_raising(self, monkeypatch):
        """A missing GPU must not take the whole app down at startup."""
        self._stub_torch(monkeypatch, available=False)
        assert resolve_rerank_device("cuda") == "cpu"


# ---------------------------------------------------------------------------
# safe_filename -- the path-traversal guard every filesystem-bound filename
# must pass through (see CLAUDE.md Critical Constraints)
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_plain_filename_passes_through(self):
        assert safe_filename("report.pdf") == "report.pdf"

    def test_strips_posix_traversal(self):
        assert safe_filename("../../etc/passwd") == "passwd"

    @pytest.mark.skipif(os.name != "nt", reason="backslash is only a path separator on Windows; on POSIX it's a literal filename character, not a traversal vector")
    def test_strips_windows_traversal(self):
        assert safe_filename("..\\..\\windows\\system32\\config") == "config"
        assert safe_filename("C:\\Windows\\evil.dll") == "evil.dll"
        # basename of a bare "..\\" is "" on Windows (backslash is a
        # separator there), so it must be rejected same as "..";
        # on POSIX "..\\" is a literal, valid filename -- see
        # test_rejects_empty_and_dot_names, which is intentionally
        # platform-neutral and does not include this case.
        with pytest.raises(ValueError):
            safe_filename("..\\")

    def test_strips_absolute_path(self):
        assert safe_filename("/etc/shadow") == "shadow"

    def test_strips_surrounding_whitespace(self):
        assert safe_filename("  doc.txt  ") == "doc.txt"

    @pytest.mark.parametrize("bad", ["", "   ", ".", "..", "../", None])
    def test_rejects_empty_and_dot_names(self, bad):
        with pytest.raises(ValueError):
            safe_filename(bad)

    # A path that is nothing but separators has no filename component at all:
    # basename() returns "" rather than a dot-name, so it exercises a
    # different branch of the guard than the cases above. Platform-neutral --
    # "/" is a separator everywhere.
    @pytest.mark.parametrize("bad", ["/", "/etc/", "   /   "])
    def test_rejects_paths_with_no_filename_component(self, bad):
        with pytest.raises(ValueError):
            safe_filename(bad)


# ---------------------------------------------------------------------------
# TextChunker -- regression tests for the duplicate-tail bug (Phase 10) and
# the no-forward-progress guard
# ---------------------------------------------------------------------------

class TestTextChunker:
    def test_empty_text_returns_no_chunks(self):
        assert TextChunker.chunk("") == []

    def test_short_text_is_single_chunk_no_duplicate_tail(self):
        # Pre-fix, a document shorter than chunk_size produced 2 chunks: the
        # full text plus an overlapping duplicate of its own tail.
        text = "A short receipt: wheels $2,400 and console $3,500."
        chunks = TextChunker.chunk(text, chunk_size=800, chunk_overlap=150)
        assert chunks == [text]

    def test_long_text_covers_content_without_duplicate_final_chunk(self):
        text = " ".join(f"word{i}" for i in range(1000))
        chunks = TextChunker.chunk(text, chunk_size=200, chunk_overlap=50)
        assert len(chunks) > 1
        # Every chunk is real content, the last chunk is not a strict suffix
        # duplicate of the one before it, and nothing was dropped.
        assert all(c.strip() for c in chunks)
        assert not chunks[-2].endswith(chunks[-1])
        assert chunks[0].startswith("word0 ")
        assert chunks[-1].endswith("word999")

    def test_consecutive_chunks_overlap(self):
        text = " ".join(f"word{i}" for i in range(1000))
        chunks = TextChunker.chunk(text, chunk_size=200, chunk_overlap=50)
        for a, b in zip(chunks, chunks[1:], strict=False):
            # The start of each chunk repeats the tail of the previous one.
            assert b.split()[0] in a

    def test_no_infinite_loop_on_pathological_input(self):
        # A run with no whitespace exercises the boundary-snap fallback; must
        # terminate and still return content.
        text = "x" * 5000
        chunks = TextChunker.chunk(text, chunk_size=200, chunk_overlap=50)
        assert chunks
        assert "".join(chunks).startswith("x")


# ---------------------------------------------------------------------------
# SQLiteVectorStore -- write/delete invariants and hybrid search, with a
# deterministic fake embedder (no torch)
# ---------------------------------------------------------------------------

class FakeEmbeddingEngine:
    """Deterministic 384-dim embeddings from a seeded hash of the text, so
    identical text always embeds identically and search is reproducible."""

    def _embed(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        vec = rng.standard_normal(384).astype(np.float32)
        return vec / np.linalg.norm(vec)

    def embed_chunks(self, chunks):
        return [self._embed(c) for c in chunks]

    def embed_query(self, query):
        return self._embed(query)


@pytest.fixture
def store(tmp_path, monkeypatch):
    # add_document/delete_document write physical copies to the store's
    # storage_dir, which now defaults to a repo-root-anchored path -- pass an
    # explicit temp dir (and chdir for the relative-path assertions below)
    # so every test stays hermetic.
    monkeypatch.chdir(tmp_path)
    return SQLiteVectorStore(
        db_path=str(tmp_path / "test.db"),
        storage_dir=str(tmp_path / "stored_documents"),
    )


@pytest.fixture
def embedder():
    return FakeEmbeddingEngine()


def _counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        parents = conn.execute("SELECT COUNT(*) FROM parent_chunks").fetchone()[0]
        children = conn.execute("SELECT COUNT(*) FROM child_chunks").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM parent_chunks_fts").fetchone()[0]
        orphan_parents = conn.execute(
            "SELECT COUNT(*) FROM parent_chunks WHERE document_id NOT IN (SELECT id FROM documents)"
        ).fetchone()[0]
        orphan_children = conn.execute(
            "SELECT COUNT(*) FROM child_chunks WHERE parent_id NOT IN (SELECT id FROM parent_chunks)"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "docs": docs, "parents": parents, "children": children, "fts": fts,
        "orphan_parents": orphan_parents, "orphan_children": orphan_children,
    }


LABOR_TEXT = (
    "Regional labor rate schedule 2026. The maximum allowed mechanical labor "
    "rate for Nevada is $110 per hour. Sheet metal repair is capped at $62 "
    "per hour and refinishing at $62 per hour statewide."
)
GLASS_TEXT = (
    "Zero-deductible glass endorsement. Windshield replacement with OEM spec "
    "glass waives the comprehensive deductible entirely for safety glass."
)


class TestSQLiteVectorStore:
    def test_add_document_indexes_chunks_and_fts(self, store, embedder):
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        c = _counts(store.db_path)
        assert c["docs"] == 1
        assert c["parents"] >= 1
        assert c["children"] >= 1
        assert c["fts"] == c["parents"]

    def test_delete_document_cascades_no_orphans(self, store, embedder):
        # Regression: PRAGMA foreign_keys defaults OFF per-connection, so ON
        # DELETE CASCADE silently never fired until _connect() enabled it --
        # deletes left orphaned parent/child chunk rows behind.
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store.add_document("glass.txt", "txt", 100, GLASS_TEXT, embedder)
        assert store.delete_document("labor.txt") is True
        c = _counts(store.db_path)
        assert c["docs"] == 1
        assert c["orphan_parents"] == 0
        assert c["orphan_children"] == 0
        assert c["fts"] == c["parents"]

    def test_overwrite_same_filename_does_not_leak_chunks(self, store, embedder):
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        before = _counts(store.db_path)
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        after = _counts(store.db_path)
        assert after == before

    def test_add_document_sanitizes_traversal_filename(self, store, embedder):
        store.add_document("../../evil.txt", "txt", 100, LABOR_TEXT, embedder)
        docs = store.get_all_documents()
        assert [d["filename"] for d in docs] == ["evil.txt"]

    def test_hybrid_search_returns_results_with_expected_shape(self, store, embedder):
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store.add_document("glass.txt", "txt", 100, GLASS_TEXT, embedder)
        q = "mechanical labor rate cap Nevada"
        results = store.search_similarity(embedder.embed_query(q), q, top_k=2)
        assert results
        assert {"content", "filename", "file_type", "score"} <= set(results[0])

    def test_fts_keyword_leg_surfaces_exact_term_match(self, store, embedder):
        # The fake embedder is semantically blind (hash-based), so an exact
        # keyword hit landing in the results proves the FTS5 leg + RRF fusion
        # is doing the work.
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store.add_document("glass.txt", "txt", 100, GLASS_TEXT, embedder)
        q = "windshield glass deductible"
        results = store.search_similarity(embedder.embed_query(q), q, top_k=2)
        assert "glass.txt" in [r["filename"] for r in results]

    def test_search_after_delete_serves_fresh_results(self, store, embedder):
        # Regression guard for the vector-cache invalidation constraint.
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        q = "labor rate"
        assert store.search_similarity(embedder.embed_query(q), q, top_k=4)
        store.delete_document("labor.txt")
        assert store.search_similarity(embedder.embed_query(q), q, top_k=4) == []

    def test_claim_scoping_isolates_claims(self, store, embedder):
        store.add_document("global.txt", "txt", 100, LABOR_TEXT, embedder)
        store.add_document("receipt.txt", "txt", 100, GLASS_TEXT, embedder,
                           claim_id="#2026-1")
        q = "windshield glass deductible"
        emb = embedder.embed_query(q)
        other_claim = store.search_similarity(emb, q, claim_id="#2026-2", top_k=10)
        assert "receipt.txt" not in [r["filename"] for r in other_claim]
        own_claim = store.search_similarity(emb, q, claim_id="#2026-1", top_k=10)
        assert "receipt.txt" in [r["filename"] for r in own_claim]

    def test_get_claim_chunks_returns_all_chunks_with_sentinel_score(self, store, embedder):
        store.add_document("receipt.txt", "txt", 100, GLASS_TEXT, embedder,
                           claim_id="#2026-1")
        chunks = store.get_claim_chunks("#2026-1")
        assert chunks
        assert all(ch["score"] == 1.0 for ch in chunks)
        assert store.get_claim_chunks("#2026-nope") == []

    def test_failed_overwrite_leaves_disk_matching_rolled_back_db(self, store, embedder):
        # Regression: physical-file writes used to happen *inside* the same
        # try block as chunk/embedding inserts. A failure after the file was
        # already replaced (e.g. embedding generation raising) rolled the DB
        # back to the OLD document while disk permanently kept the NEW
        # (failed, partial) content -- a silent desync between what's
        # indexed/searchable and what a handler sees opening the file.
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        stored_path = os.path.join(store.storage_dir, store.get_blob_key("labor.txt") or "labor.txt")
        with open(stored_path) as f:
            assert f.read() == LABOR_TEXT

        class FailingEmbedder:
            def embed_chunks(self, chunks):
                raise RuntimeError("simulated embedding failure mid-overwrite")

        with pytest.raises(RuntimeError):
            store.add_document("labor.txt", "txt", 100, "REPLACED CONTENT", FailingEmbedder())

        # DB rolled back to the original document...
        docs = store.get_all_documents()
        assert len(docs) == 1
        c = _counts(store.db_path)
        assert c["docs"] == 1
        # ...so the physical file must still match it, not the failed write.
        with open(stored_path) as f:
            assert f.read() == LABOR_TEXT

    def test_overwrite_without_file_path_always_refreshes_disk_content(self, store, embedder):
        # Regression: the fallback (no file_path) write only fired
        # `if not os.path.exists(dest_path)`, so overwriting a document added
        # without a physical source silently kept serving the OLD file
        # content forever while the DB/search index moved on to the NEW text.
        store.add_document("notes.txt", "txt", 100, "first version", embedder)
        store.add_document("notes.txt", "txt", 100, "second version", embedder)
        stored_path = os.path.join(store.storage_dir, store.get_blob_key("notes.txt") or "notes.txt")
        with open(stored_path) as f:
            assert f.read() == "second version"

    def test_cross_claim_same_filename_is_rejected_not_data_loss(self, store, embedder):
        # Regression: the documents table keys on filename alone, so an
        # overwrite used to silently REPLACE claim A's document when the same
        # filename was uploaded to claim B (row, chunks, and physical file
        # all deleted). Scope-mismatched overwrites must be refused outright.
        store.add_document("report.pdf", "pdf", 100, LABOR_TEXT, embedder, claim_id="#CLAIM-A")
        with pytest.raises(ValueError, match="already exists"):
            store.add_document("report.pdf", "pdf", 100, GLASS_TEXT, embedder, claim_id="#CLAIM-B")
        # Claim A's document is untouched (DB + physical file).
        assert [d["filename"] for d in store.get_claim_documents("#CLAIM-A")] == ["report.pdf"]
        stored_path = os.path.join(store.storage_dir, store.get_blob_key("report.pdf") or "report.pdf")
        with open(stored_path) as f:
            assert f.read() == LABOR_TEXT
        # Global-vs-claim collisions are refused too (both directions).
        with pytest.raises(ValueError):
            store.add_document("report.pdf", "pdf", 100, LABOR_TEXT, embedder)
        store.add_document("global.pdf", "pdf", 100, LABOR_TEXT, embedder)
        with pytest.raises(ValueError):
            store.add_document("global.pdf", "pdf", 100, GLASS_TEXT, embedder, claim_id="#CLAIM-A")

    def test_same_scope_overwrite_still_allowed(self, store, embedder):
        store.add_document("report.pdf", "pdf", 100, LABOR_TEXT, embedder, claim_id="#CLAIM-A")
        # Same claim re-upload must keep working (this is the legitimate
        # overwrite path used when a handler replaces a dossier file).
        store.add_document("report.pdf", "pdf", 100, GLASS_TEXT, embedder, claim_id="#CLAIM-A")
        assert [d["filename"] for d in store.get_claim_documents("#CLAIM-A")] == ["report.pdf"]
        stored_path = os.path.join(store.storage_dir, store.get_blob_key("report.pdf") or "report.pdf")
        with open(stored_path) as f:
            assert f.read() == GLASS_TEXT

    def test_fts_reserved_word_queries_do_not_silently_drop_keyword_leg(self, store, embedder):
        # FTS5 operator words (AND/OR/NOT/NEAR) passed bare are parsed as
        # query syntax, previously raising an OperationalError that was
        # swallowed -- dropping the keyword leg for exactly the queries that
        # need it. Quoted tokens must match as literals instead.
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store.add_document("glass.txt", "txt", 100, GLASS_TEXT, embedder)
        for q in ["and", "or", "not", "NEAR", "deductible or not covered"]:
            results = store.search_similarity(embedder.embed_query(q), q, top_k=4)
            assert results  # never crashes, never returns empty due to syntax

    def test_no_partial_file_left_on_successful_write(self, store, embedder):
        store.add_document("clean.txt", "txt", 100, LABOR_TEXT, embedder)
        # The staged *.part file must be gone after a successful add.
        assert not os.path.exists(os.path.join(store.storage_dir, store.get_blob_key("clean.txt.part") or "clean.txt.part"))
