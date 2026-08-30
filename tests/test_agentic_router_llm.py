from backend.agentic_router import AgenticRAGRouter
from backend.llm_client import ChatClient


class RecordingClient(ChatClient):
    def __init__(self):
        self.plans = []
        self.synthesis = []
        self.models_calls = 0
    def models(self):
        self.models_calls += 1
        return ["M"]
    def model_for_stage(self, stage):
        return "M"
    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        record = {"model": model, "temperature": temperature,
                  "max_tokens": max_tokens, "messages": messages, "stage": stage}
        if "Claims Planner" in messages[0]["content"]:
            self.plans.append(record)
            return '{"needs_global_policies": true, "needs_claim_dossier": false, ' \
                   '"sub_queries": ["labor"]}'
        self.synthesis.append(record)
        return "The Nevada mechanical cap is $110/hr."
    def complete_stream(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        yield self.complete(messages, model=model, temperature=temperature,
                             max_tokens=max_tokens, stage=stage)


class FakeVectorStore:
    def search_similarity(self, qe, qt, claim_id=None, reranking_engine=None, top_k=4, **kw):
        return [{"id": 1, "content": "Nevada mechanical labor cap is $110.",
                "filename": "labor.txt", "file_type": "txt", "score": 0.9},
               {"id": 2, "content": "California mechanical cap is $120.",
                "filename": "labor2.txt", "file_type": "txt", "score": 0.7}]
    def get_claim_chunks(self, claim_id):
        return []
    def get_all_documents(self):
        return []


class FakeEmbedder:
    def embed_query(self, q):
        return [0.0] * 384


def test_online_path_uses_per_stage_models_and_returns_answer():
    router = AgenticRAGRouter()
    client = RecordingClient()
    result = router.run_query(
        query_text="what is the labor cap?",
        claim_id=None,
        engine="lm-studio",
        embedding_engine=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        reranking_engine=None,
        llm_client=client,
    )
    assert result["answer"] == "The Nevada mechanical cap is $110/hr."
    assert len(client.plans) == 1
    assert len(client.synthesis) == 1
    assert client.plans[0]["model"] == "M"
    assert client.plans[0]["temperature"] == 0.0
    assert client.plans[0]["max_tokens"] == 150
    assert client.plans[0]["stage"] == "planning"  # planner gets the short timeout
    assert client.synthesis[0]["model"] == "M"
    assert client.synthesis[0]["temperature"] == 0.1
    assert client.synthesis[0]["max_tokens"] == 1000
    assert client.synthesis[0]["stage"] == "synthesis"
    assert "Nevada" in result["answer"]


def test_engine_allowlist_rejects_unknown_without_call():
    router = AgenticRAGRouter()
    client = RecordingClient()
    result = router.run_query(
        query_text="q", claim_id=None, engine="https://evil.example/x",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    )
    assert "Unknown engine" in result["answer"]
    assert client.plans == [] and client.synthesis == []
