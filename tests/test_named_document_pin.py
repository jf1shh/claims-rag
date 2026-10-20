from backend.rag_engine import SQLiteVectorStore, pin_named_documents


def _doc(doc_id, filename, claim_id=None):
    return {"id": doc_id, "filename": filename, "claim_id": claim_id}


def test_subrogation_liability_plural_pins_both_sops():
    ranked = [
        _doc(1, "Case_Study_A.pdf"),
        _doc(2, "Case_Study_B.pdf"),
        _doc(3, "Auto_Claims_Guidelines.docx"),
        _doc(4, "Claim_Statement.pdf", claim_id=123),
        _doc(5, "SOP_Coverage_Verification.pdf"),
        _doc(6, "SOP_Liability_Determination.pdf"),
        _doc(7, "SOP_Subrogation_Recovery.pdf"),
    ]
    query = "Using the police report, statements and the Subrogation and Liability SOPs, say whether..."

    result = pin_named_documents(query, ranked, top_k=4)

    filenames = [r["filename"] for r in result]
    assert filenames == ["Case_Study_A.pdf", "Case_Study_B.pdf", "SOP_Liability_Determination.pdf", "SOP_Subrogation_Recovery.pdf"]
    assert result[2]["pinned"] == "named_document"
    assert result[3]["pinned"] == "named_document"
    assert "pinned" not in result[0]
    assert "pinned" not in result[1]


def test_fraud_red_flags_possessive_pins_sop():
    ranked = [
        _doc(1, "Case_Study_X.pdf"),
        _doc(2, "Case_Study_Y.pdf"),
        _doc(3, "Claim_Notes.pdf", claim_id=55),
        _doc(4, "SOP_Claims_Fraud_Red_Flags.pdf"),
    ]
    query = "Screen the Fraud Red Flags SOP's chronology for indicators"

    result = pin_named_documents(query, ranked, top_k=3)

    filenames = [r["filename"] for r in result]
    assert filenames == ["Case_Study_X.pdf", "Case_Study_Y.pdf", "SOP_Claims_Fraud_Red_Flags.pdf"]
    assert result[2]["pinned"] == "named_document"


def test_no_type_word_leaves_selection_unchanged():
    ranked = [
        _doc(1, "Case_Study_X.pdf"),
        _doc(2, "Case_Study_Y.pdf"),
        _doc(3, "SOP_Subrogation_Recovery.pdf"),
    ]
    query = "please review this filing for completeness"

    result = pin_named_documents(query, ranked, top_k=2)

    assert result == ranked[:2]


def test_claim_scoped_document_never_pinned():
    ranked = [
        _doc(1, "Case_Study_X.pdf"),
        _doc(2, "Case_Study_Y.pdf"),
        _doc(3, "SOP_Liability_Determination.pdf", claim_id=10),
    ]
    query = "the Liability SOP governs this"

    result = pin_named_documents(query, ranked, top_k=2)

    assert result == ranked[:2]


def test_already_present_document_not_duplicated():
    ranked = [
        _doc(1, "SOP_Subrogation_Recovery.pdf"),
        _doc(2, "Case_Study_Y.pdf"),
        _doc(3, "SOP_Liability_Determination.pdf"),
    ]
    query = "the Subrogation and Liability SOPs apply here"

    result = pin_named_documents(query, ranked, top_k=2)

    filenames = [r["filename"] for r in result]
    assert filenames == ["SOP_Subrogation_Recovery.pdf", "SOP_Liability_Determination.pdf"]
    assert "pinned" not in result[0]
    assert result[1]["pinned"] == "named_document"


def test_max_pins_respected():
    ranked = [
        _doc(1, "Case_Study_A.pdf"),
        _doc(2, "Case_Study_B.pdf"),
        _doc(3, "Case_Study_C.pdf"),
        _doc(4, "SOP_Liability_Determination.pdf"),
        _doc(5, "SOP_Subrogation_Recovery.pdf"),
        _doc(6, "SOP_Coverage_Verification.pdf"),
    ]
    query = "the Liability, Subrogation and Coverage SOPs all apply"

    result = pin_named_documents(query, ranked, top_k=3, max_pins=1)

    pinned = [r for r in result if r.get("pinned") == "named_document"]
    assert len(pinned) == 1


def test_output_order_follows_ranked_positions():
    ranked = [
        _doc(1, "Case_Study_A.pdf"),
        _doc(2, "Case_Study_B.pdf"),
        _doc(3, "Case_Study_C.pdf"),
        _doc(4, "SOP_Subrogation_Recovery.pdf"),
    ]
    query = "the Subrogation SOP applies"

    result = pin_named_documents(query, ranked, top_k=3)

    # Pinned item (SOP, ranked index 3) sorts after the two higher-ranked case
    # studies it replaced the lowest of, preserving monotone rank order.
    assert [r["filename"] for r in result] == ["Case_Study_A.pdf", "Case_Study_B.pdf", "SOP_Subrogation_Recovery.pdf"]


def test_pinned_key_only_on_pins_and_input_not_mutated():
    ranked = [
        _doc(1, "Case_Study_A.pdf"),
        _doc(2, "Case_Study_B.pdf"),
        _doc(3, "SOP_Subrogation_Recovery.pdf"),
    ]
    original_snapshot = [dict(d) for d in ranked]
    query = "the Subrogation SOP applies"

    result = pin_named_documents(query, ranked, top_k=2)

    assert ranked == original_snapshot
    assert all("pinned" not in d for d in ranked)
    assert result[0].get("pinned") is None
    assert result[1]["pinned"] == "named_document"


def test_auto_claims_guidelines_never_pinned():
    ranked = [
        _doc(1, "Case_Study_A.pdf"),
        _doc(2, "Case_Study_B.pdf"),
        _doc(3, "Auto_Claims_Guidelines.docx"),
    ]
    query = "the Auto Claims Guideline plan applies"

    result = pin_named_documents(query, ranked, top_k=2)

    assert result == ranked[:2]


def test_top_k_greater_or_equal_to_len_returns_unchanged():
    ranked = [
        _doc(1, "Case_Study_A.pdf"),
        _doc(2, "SOP_Subrogation_Recovery.pdf"),
    ]
    query = "the Subrogation SOP applies"

    assert pin_named_documents(query, ranked, top_k=2) == ranked[:2]
    assert pin_named_documents(query, ranked, top_k=5) == ranked[:5]
    assert pin_named_documents(query, ranked, top_k=0) == []


_UNIT = [1.0] + [0.0] * 383  # the vector cache keeps only 384-dim rows


class _Embedder:
    def embed_query(self, query):
        return _UNIT

    def embed_chunks(self, chunks):
        return [_UNIT for _ in chunks]


class _RankSopLastReranker:
    """Fixed ranking that puts the named SOP document last, like the real
    cross-encoder observed on the actual corpus (titles alone don't help it)."""

    _ORDER = {
        "Case_Study_Alpha.txt": 3,
        "Case_Study_Beta.txt": 2,
        "SOP_Subrogation_Recovery.txt": 1,
    }

    def rerank(self, query, passages, top_k=4):
        scored = [dict(p, rerank_score=float(self._ORDER.get(p["filename"], 0))) for p in passages]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]


def test_sqlite_store_pins_named_sop_into_top_k(tmp_path):
    store = SQLiteVectorStore(str(tmp_path / "store.db"), str(tmp_path / "docs"))
    embedder = _Embedder()
    store.add_document("Case_Study_Alpha.txt", "txt", 1, "alpha case study content", embedder)
    store.add_document("Case_Study_Beta.txt", "txt", 1, "beta case study content", embedder)
    store.add_document("SOP_Subrogation_Recovery.txt", "txt", 1, "subrogation recovery procedure content", embedder)

    query = "Using the Subrogation SOP, confirm subrogation recovery eligibility"
    reranker = _RankSopLastReranker()

    results = store.search_similarity(_UNIT, query, reranking_engine=reranker, top_k=2)
    filenames = [r["filename"] for r in results]
    assert "SOP_Subrogation_Recovery.txt" in filenames
    assert len(filenames) == 2

    naive_results = store.search_similarity(_UNIT, query, reranking_engine=reranker, top_k=2, use_fts=False)
    naive_filenames = [r["filename"] for r in naive_results]
    assert naive_filenames == ["Case_Study_Alpha.txt", "Case_Study_Beta.txt"]


def test_same_named_claim_file_does_not_block_global_pin():
    ranked = [
        {"id": 1, "filename": "police_report_Webb.pdf", "claim_id": "#2026-71845"},
        {"id": 2, "filename": "SOP_Subrogation_Recovery.pdf", "claim_id": "#2026-71845"},
        {"id": 3, "filename": "Case_Study_Rear_End_Collision.pdf", "claim_id": None},
        {"id": 4, "filename": "SOP_Subrogation_Recovery.pdf", "claim_id": None},
    ]
    out = pin_named_documents("Using the Subrogation SOP, triage the claim", ranked, top_k=2)
    assert [d["id"] for d in out] == [1, 4]
    assert out[1]["pinned"] == "named_document"
