from backend.rag_engine import CLAIM_RERANK_CANDIDATES, SQLiteVectorStore, add_claim_candidates

# The vector cache keeps only 384-dim rows. Global docs point along the query,
# the claim statement is orthogonal, so it ranks last in the fused list.
_QUERY = [1.0] + [0.0] * 383
_FAR = [0.0, 1.0] + [0.0] * 382


def _doc(doc_id, claim_id=None):
    return {"id": doc_id, "filename": f"doc{doc_id}.pdf", "claim_id": claim_id}


def test_adds_claim_items_not_already_in_the_pool():
    pool = [_doc(1), _doc(2, "C1")]
    claim_ranked = [_doc(2, "C1"), _doc(3, "C1")]

    result = add_claim_candidates(pool, claim_ranked)

    assert [d["id"] for d in result] == [1, 2, 3]


def test_caps_the_number_of_added_items():
    claim_ranked = [_doc(i, "C1") for i in range(10, 30)]

    result = add_claim_candidates([_doc(1)], claim_ranked)

    assert len(result) == 1 + CLAIM_RERANK_CANDIDATES
    assert [d["id"] for d in result[1:]] == list(range(10, 10 + CLAIM_RERANK_CANDIDATES))


def test_returns_pool_unchanged_when_nothing_to_add():
    pool = [_doc(1), _doc(2, "C1")]

    assert add_claim_candidates(pool, [_doc(2, "C1")]) is pool
    assert add_claim_candidates(pool, []) is pool


class _Embedder:
    def embed_query(self, query):
        return _QUERY

    def embed_chunks(self, chunks):
        return [_FAR if "deer" in c else _QUERY for c in chunks]


class _PreferStatementReranker:
    """The cross-encoder ranks the narrative statement first once it sees it."""

    def __init__(self):
        self.seen = []

    def rerank(self, query, passages, top_k=4):
        self.seen = [p["filename"] for p in passages]
        scored = [dict(p, rerank_score=2.0 if "statement" in p["filename"] else 1.0) for p in passages]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]


def _store(tmp_path):
    store = SQLiteVectorStore(str(tmp_path / "store.db"), str(tmp_path / "docs"))
    embedder = _Embedder()
    for name in ("SOP_A.txt", "SOP_B.txt", "SOP_C.txt"):
        store.add_document(name, "txt", 1, f"{name} checklist indicators referral", embedder)
    store.add_document("loss_statement.txt", "txt", 1, "a deer ran into the road at 2:40 AM", embedder, claim_id="C1")
    store.add_document("loss_statement_other.txt", "txt", 1, "a deer on another claim", embedder, claim_id="C2")
    return store


QUERY = "Run the SIU indicator checklist and say whether a referral is indicated"


def test_claim_document_outside_the_pool_still_reaches_the_reranker(tmp_path):
    store = _store(tmp_path)
    reranker = _PreferStatementReranker()

    results = store.search_similarity(_QUERY, QUERY, claim_id="C1", reranking_engine=reranker, top_k=2, candidate_pool=2)

    assert "loss_statement.txt" in reranker.seen
    assert results[0]["filename"] == "loss_statement.txt"
    # Another claim's file is never a candidate.
    assert all(r["claim_id"] in (None, "C1") for r in results)
    assert "loss_statement_other.txt" not in reranker.seen


def test_no_claim_candidates_without_a_claim_or_in_naive_mode(tmp_path):
    store = _store(tmp_path)
    reranker = _PreferStatementReranker()

    store.search_similarity(_QUERY, QUERY, reranking_engine=reranker, top_k=2, candidate_pool=2)
    assert not any("statement" in f for f in reranker.seen)

    store.search_similarity(_QUERY, QUERY, claim_id="C1", reranking_engine=reranker, top_k=2, candidate_pool=2, use_fts=False)
    assert "loss_statement.txt" not in reranker.seen
