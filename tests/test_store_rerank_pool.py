from backend.rag_engine import SQLiteVectorStore


class Embedder:
    def embed_query(self, query):
        return [1.0, 0.0]

    def embed_chunks(self, chunks):
        return [[1.0, 0.0] for _ in chunks]


class RecordingReranker:
    def __init__(self):
        self.pool = None

    def rerank(self, query, passages, top_k=4):
        self.pool = len(passages)
        return [dict(p, rerank_score=1.0) for p in passages[:top_k]]


def test_candidate_pool_is_passed_to_reranker(tmp_path):
    store = SQLiteVectorStore(str(tmp_path / "store.db"), str(tmp_path / "docs"))
    embedder = Embedder()
    for filename in ("a.txt", "b.txt", "c.txt"):
        store.add_document(filename, "txt", 1, f"content {filename}", embedder)

    reranker = RecordingReranker()
    store.search_similarity(
        [1.0, 0.0], "content", reranking_engine=reranker, top_k=1, candidate_pool=2
    )

    assert reranker.pool == 2
