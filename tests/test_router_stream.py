from backend.agentic_router import AgenticRAGRouter
from tests.test_agentic_router_llm import FakeVectorStore, FakeEmbedder


class StreamingClient:
    """Fake ChatClient for the streaming path. Implements the same
    model_for_stage/stage-kwarg surface as RecordingClient in
    test_agentic_router_llm.py, since _online_pipeline's planner call
    (_get_llm_plan) requires it regardless of JSON vs. streaming entrypoint."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.plans = 0

    def models(self):
        return ["M"]

    def model_for_stage(self, stage):
        return "M"

    def complete_stream(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        for t in self.tokens:
            yield t

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        self.plans += 1
        return '{"needs_global_policies": true, "needs_claim_dossier": false, "sub_queries": ["labor"]}'


def test_run_query_stream_emits_chunks_then_final():
    router = AgenticRAGRouter()
    client = StreamingClient(["Nevada", " cap ", "is $110"])
    from config import Settings
    events = list(router.run_query_stream(
        query_text="cap?", claim_id=None, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client, caps=Settings.from_env({}),
    ))
    chunks = [e for e in events if e["type"] == "chunk"]
    finals = [e for e in events if e["type"] == "final"]
    assert [c["text"] for c in chunks] == ["Nevada", " cap ", "is $110"]
    assert len(finals) == 1
    assert finals[0]["answer"] == "Nevada cap is $110"
    assert "ttf_ms" in finals[0]
    assert finals[0]["engine"] == "lm-studio (agentic)"
