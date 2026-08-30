import time

from backend.agentic_router import AgenticRAGRouter
from backend.llm_client import ChatClient, ChatClientError


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


# ---------------------------------------------------------------------------
# run_query_stream: ttf_ms must measure time-to-FIRST-token, not total
# elapsed time (whole-branch-review finding).
# ---------------------------------------------------------------------------

class SlowStreamClient(ChatClient):
    """Planner responds instantly; the token stream is deliberately slow
    between the first and second chunk, so a bug that measures total elapsed
    time (instead of time-to-first-token) is unambiguously distinguishable
    from the fix."""

    def models(self):
        return ["m"]

    def model_for_stage(self, stage):
        return "m"

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        return ('{"needs_global_policies": true, "needs_claim_dossier": false, '
                '"sub_queries": ["labor"]}')

    def complete_stream(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        time.sleep(0.05)  # time to first token
        yield "Hello "
        time.sleep(0.2)   # additional generation time after the first token
        yield "world"


def test_ttf_ms_measures_time_to_first_token_not_total_elapsed():
    router = AgenticRAGRouter()
    client = SlowStreamClient()
    events = list(router.run_query_stream(
        query_text="what is the labor cap?", claim_id=None, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    final = events[-1]
    assert final["type"] == "final"
    assert final["answer"] == "Hello world"
    # Total generation time is ~250ms (50ms + 200ms). If ttf_ms measured
    # total elapsed time (the bug), it would be >= 250ms here. The real
    # time-to-first-token is ~50ms.
    assert final["ttf_ms"] < 150


# ---------------------------------------------------------------------------
# run_query_stream: an LLM failure before any token streamed falls back to a
# real, grounded simulated answer -- mirroring _run_online_agent's
# ChatClientError -> _run_simulated_agent behavior -- instead of an empty
# assistant bubble (whole-branch-review finding).
# ---------------------------------------------------------------------------

class UnreachableStreamClient(ChatClient):
    def models(self):
        return ["m"]

    def model_for_stage(self, stage):
        return "m"

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        return ('{"needs_global_policies": true, "needs_claim_dossier": false, '
                '"sub_queries": ["labor"]}')

    def complete_stream(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        raise ChatClientError("LM Studio unreachable")
        yield  # pragma: no cover -- makes this a generator function


def test_stream_falls_back_to_simulated_agent_on_immediate_llm_failure():
    router = AgenticRAGRouter()
    client = UnreachableStreamClient()
    events = list(router.run_query_stream(
        query_text="what is the labor cap?", claim_id=None, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    # No chunk events -- the failure happened before any token streamed.
    assert all(e["type"] != "chunk" for e in events)
    assert len(events) == 1
    final = events[0]
    assert final["type"] == "final"
    assert final["status"] == "simulated"
    assert final["engine"] == "simulated (agentic)"
    # A real, grounded answer -- not an empty bubble.
    assert final["answer"]


class MidStreamFailureClient(ChatClient):
    """Streams one token, then the connection dies -- there is no clean way
    to "undo" an already-streamed token, so this must keep the existing
    partial-answer + status=error behavior rather than falling back."""

    def models(self):
        return ["m"]

    def model_for_stage(self, stage):
        return "m"

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        return ('{"needs_global_policies": true, "needs_claim_dossier": false, '
                '"sub_queries": ["labor"]}')

    def complete_stream(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        yield "Partial answer"
        raise ChatClientError("connection dropped mid-stream")


def test_stream_keeps_partial_answer_and_error_status_when_tokens_already_sent():
    router = AgenticRAGRouter()
    client = MidStreamFailureClient()
    events = list(router.run_query_stream(
        query_text="what is the labor cap?", claim_id=None, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    assert any(e["type"] == "chunk" for e in events)
    final = events[-1]
    assert final["type"] == "final"
    assert final["status"] == "error"
    assert final["answer"] == "Partial answer"


# ---------------------------------------------------------------------------
# run_query_stream: a pipeline failure *before* synthesis starts (embedding
# or retrieval raising) must still yield exactly one `final` event -- never
# propagate an uncaught exception out of the generator -- and must never leak
# the raw exception text to the client (whole-branch-review finding).
# ---------------------------------------------------------------------------

class ExplodingVectorStore:
    def search_similarity(self, *a, **kw):
        raise RuntimeError("db connection lost: boom")

    def get_claim_chunks(self, claim_id):
        raise RuntimeError("db connection lost: boom")


def test_stream_pipeline_failure_before_synthesis_still_yields_one_final_event():
    router = AgenticRAGRouter()
    client = RecordingClient()
    events = list(router.run_query_stream(
        query_text="what is the labor cap?", claim_id=None, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=ExplodingVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    assert len(events) == 1
    final = events[0]
    assert final["type"] == "final"
    assert final["status"] == "error"
    # Never leak the raw exception text to the client.
    assert "boom" not in final["answer"]
    # complete_stream was never reached -- the failure happened during
    # retrieval, before synthesis started.
    assert client.synthesis == []


# ---------------------------------------------------------------------------
# run_query_stream: Critical Constraints (engine allowlist + zero-context
# hard stop) apply on the streaming path too, not only run_query's JSON path
# (whole-branch-review finding: this coverage previously existed only for
# run_query).
# ---------------------------------------------------------------------------

def test_stream_engine_allowlist_rejects_unknown_without_call():
    router = AgenticRAGRouter()
    client = RecordingClient()
    events = list(router.run_query_stream(
        query_text="q", claim_id=None, engine="https://evil.example/x",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    assert len(events) == 1
    final = events[0]
    assert final["type"] == "final"
    assert final["status"] == "rejected"
    assert "Unknown engine" in final["answer"]
    assert client.plans == [] and client.synthesis == []


class EmptyVectorStore:
    def search_similarity(self, *a, **kw):
        return []

    def get_claim_chunks(self, claim_id):
        return []


class NoCompleteStreamClient(RecordingClient):
    def complete_stream(self, *a, **kw):
        raise AssertionError("complete_stream must not be called when no context was retrieved")
        yield  # pragma: no cover -- makes this a generator function


def test_stream_zero_context_refuses_without_calling_complete_stream():
    router = AgenticRAGRouter()
    client = NoCompleteStreamClient()
    events = list(router.run_query_stream(
        query_text="what is the labor cap?", claim_id=None, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=EmptyVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    assert len(events) == 1
    final = events[0]
    assert final["type"] == "final"
    assert final["status"] == "refused"
    assert final["sources"] == []
