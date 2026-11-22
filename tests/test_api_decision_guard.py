"""Requests that ask the assistant to make a claim decision get one explicit refusal-and-offer.

The check lives in backend/answer_guard.py (find_decision_request / apply_decision_guard) and is
wired through api.guard_result, so it covers /api/chat and /api/chat/stream for both engines.
"""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from backend.answer_guard import DECLINED_TEXT, find_decision_request
from backend.app import app
from backend.app import runtime as app_module
from backend.audit import JsonlAuditSink
from backend.rate_limit import SlidingWindowRateLimiter

DECISION_REQUESTS = [
    "Approve this claim for payment",
    "Please deny the claim",
    "Pay this claim in full",
    "Go ahead and settle this claim for $5,000",
    "Should we approve this claim?",
    "Can you decide coverage for this loss?",
    "Determine liability for this loss",
    "Set the reserve to $12,000",
    "Mark this claim as approved",
    "Reject the supplement",
]

NORMAL_QUESTIONS = [
    "What does the policy say about water damage?",
    "Summarize the adjuster notes",
    "Is there a deductible?",
    "How do I approve a supplement under the guidelines?",
    "What is the process to deny a claim?",
    "Explain why the claim was denied",
    "Pay attention to the claim notes and summarize them",
    "What are the settlement authority limits?",
    "Who was at fault according to the police report?",
    "List the documents needed for a total loss",
]


class _StubRouter:
    def __init__(self):
        self.calls = 0

    def _result(self):
        return {
            "answer": "Policy excerpt text.",
            "sources": [{"filename": "policy.txt", "content": "x", "score": 1.0}],
            "claim_dossier": None,
            "tool_section": "COMPUTED: claim approved for $9,999.",
            "engine": "lm-studio (agentic)",
            "pipeline_logs": [],
        }

    def run_query(self, **kwargs):
        self.calls += 1
        return self._result()

    def run_query_stream(self, **kwargs):
        self.calls += 1
        yield {"type": "chunk", "text": "Policy excerpt text."}
        yield {"type": "final", **self._result()}


@pytest.fixture
def client_and_sink(tmp_path, monkeypatch):
    sink_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(app_module, "_audit_sink", JsonlAuditSink(sink_path))
    monkeypatch.setattr(app_module, "_rate_limiter", SlidingWindowRateLimiter(max_requests=1000, window_seconds=60.0))
    router = _StubRouter()
    monkeypatch.setattr(app_module, "agentic_router", router)
    monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: object())
    monkeypatch.setattr(app_module, "settings", dataclasses.replace(app_module.settings, simulation_mode=True))
    return TestClient(app, raise_server_exceptions=True), sink_path, router


def _stream_final(client, query, engine):
    response = client.post("/api/chat/stream", json={"query": query, "engine": engine})
    assert response.status_code == 200
    finals = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    return finals[-1]


@pytest.mark.parametrize("engine", ["simulated", "lm-studio"])
@pytest.mark.parametrize("query", DECISION_REQUESTS)
def test_decision_request_is_refused_with_offer_and_citations(client_and_sink, query, engine):
    client, _, router = client_and_sink
    response = client.post("/api/chat", json={"query": query, "engine": engine})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == DECLINED_TEXT
    assert "don't make claim decisions" in body["answer"]
    assert "licensed adjuster" in body["answer"]
    assert "excerpts" in body["answer"] and "checklist" in body["answer"]
    assert body["guard"]["action"] == "declined"
    assert router.calls == 0  # refused before the router / model is reached
    assert "9,999" not in json.dumps(body["answer"])  # computed section not appended
    assert body["structured"]["answer"]["guard"]["action"] == "declined"
    assert body["structured"]["answer"]["decision_boundary"]["decision_status"] == "not_a_decision"


@pytest.mark.parametrize("engine", ["simulated", "lm-studio"])
@pytest.mark.parametrize("query", DECISION_REQUESTS[:3])
def test_stream_final_event_is_refused_too(client_and_sink, query, engine):
    client, _, router = client_and_sink
    final = _stream_final(client, query, engine)
    assert final["answer"] == DECLINED_TEXT
    assert final["guard"]["action"] == "declined"
    assert router.calls == 0


@pytest.mark.parametrize("query", DECISION_REQUESTS[:3])
def test_stream_for_decision_request_has_no_model_chunk(client_and_sink, query):
    client, _, router = client_and_sink
    response = client.post("/api/chat/stream", json={"query": query, "engine": "lm-studio"})
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    assert len(events) == 1 and "text" not in events[0]  # only the refusal, no chunk first
    assert events[0]["answer"] == DECLINED_TEXT
    assert "Policy excerpt text" not in response.text
    assert router.calls == 0


@pytest.mark.parametrize("engine", ["simulated", "lm-studio"])
@pytest.mark.parametrize("query", NORMAL_QUESTIONS)
def test_normal_questions_are_not_refused(client_and_sink, query, engine):
    client, _, router = client_and_sink
    body = client.post("/api/chat", json={"query": query, "engine": engine}).json()
    assert body["answer"] != DECLINED_TEXT
    assert router.calls == 1
    assert "guard" not in body or body["guard"]["action"] != "declined"
    assert find_decision_request(query) == []


def test_is_this_claim_covered_is_answered_not_refused(client_and_sink):
    """Deliberate: a coverage question is answered with excerpts, not refused."""
    client, _, router = client_and_sink
    body = client.post("/api/chat", json={"query": "Is this claim covered?", "engine": "simulated"}).json()
    assert body["answer"] != DECLINED_TEXT and router.calls == 1


def test_refusal_is_audited_with_metadata_only(client_and_sink):
    client, sink_path, router = client_and_sink
    client.post("/api/chat", json={"query": "Approve this claim for payment", "engine": "simulated"})
    events = [json.loads(x) for x in sink_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    chat = [e for e in events if e["event"] == "chat"]
    assert chat[0]["guard_action"] == "declined"
    assert "decision_request:approve_deny_pay" in chat[0]["guard_findings"]
    assert "Approve this claim" not in json.dumps(chat[0])  # no query text in the audit
