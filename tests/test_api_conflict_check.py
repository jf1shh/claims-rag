"""API-level tests for the evidence conflict check (backend/conflict_check.py).

The router and the LLM client are both stubbed: the router returns a fixed
answer over two sources, and the fake client returns a canned value-extraction
reply. These pin the wiring -- status conflicting_evidence on disagreement,
unchanged status on agreement / failure / off, precedence of a withheld guard
result, and the audit field. Extraction and comparison rules are covered by
tests/test_conflict_check.py.
"""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.app import runtime as app_module
from backend.audit import JsonlAuditSink
from backend.rate_limit import SlidingWindowRateLimiter

SOURCES = [
    {"filename": "SOP_2024.pdf", "content": "Mechanical labor is capped at $95 per hour.", "score": 1.0},
    {"filename": "SOP_2026.pdf", "content": "Mechanical labor is capped at $125 per hour.", "score": 0.9},
]
DISAGREE = json.dumps({"values": [{"source": "SOP_2024.pdf", "value": "$95 per hour"},
                                  {"source": "SOP_2026.pdf", "value": "$125 per hour"}]})
AGREE = json.dumps({"values": [{"source": "SOP_2024.pdf", "value": "$125 per hour"},
                               {"source": "SOP_2026.pdf", "value": "$125/hr"}]})


class _StubRouter:
    def __init__(self, answer):
        self.answer = answer

    def _result(self):
        return {"answer": self.answer, "sources": [dict(s) for s in SOURCES], "claim_dossier": None,
                "engine": "lm-studio (agentic)", "pipeline_logs": []}

    def run_query(self, **kwargs):
        return self._result()

    def run_query_stream(self, **kwargs):
        yield {"type": "chunk", "text": self.answer}
        yield {"type": "final", **self._result()}


class _FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def model_for_stage(self, stage):
        return "fake-model"

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def conflict_app(tmp_path, monkeypatch):
    def _build(answer="The 2026 SOP caps mechanical labor at $125 per hour.", reply=DISAGREE,
               error=None, mode="llm", guard="withhold"):
        sink_path = tmp_path / "audit.jsonl"
        client = _FakeClient(reply, error)
        monkeypatch.setattr(app_module, "_audit_sink", JsonlAuditSink(sink_path))
        # A private limiter: these tests must not spend the process-wide per-principal budget.
        monkeypatch.setattr(app_module, "_rate_limiter", SlidingWindowRateLimiter(max_requests=1000, window_seconds=60.0))
        monkeypatch.setattr(app_module, "agentic_router", _StubRouter(answer))
        # The endpoint evaluates embedding_engine=... before the stub ignores it; keep the model unloaded.
        monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: object())
        monkeypatch.setattr(app_module, "_llm_client", client)
        monkeypatch.setattr(app_module, "settings", dataclasses.replace(
            app_module.settings, conflict_check=mode, answer_guard_mode=guard))
        return TestClient(app), sink_path, client

    return _build


def _chat(client):
    response = client.post("/api/chat", json={"query": "What is the mechanical labor cap?", "engine": "simulated"})
    assert response.status_code == 200
    return response.json()


def _chat_event(sink_path):
    return [json.loads(line) for line in sink_path.read_text().splitlines() if '"chat"' in line][0]


def test_disagreeing_sources_set_conflicting_evidence(conflict_app):
    client, sink_path, fake = conflict_app()
    body = _chat(client)
    answer = body["structured"]["answer"]
    assert answer["status"] == "conflicting_evidence"
    assert answer["conflict"]["disagreement"] is True
    assert "SOP_2024.pdf gives $95 per hour" in answer["conflict"]["summary"]
    assert answer["conflict"]["summary"] in answer["interpretation"]["assumptions"]
    assert answer["interpretation"]["uncertainty"] == "medium"
    assert body["answer"].startswith("The 2026 SOP")  # the text is never rewritten by the check
    assert fake.calls == 1
    assert _chat_event(sink_path)["conflict_disagreement"] is True


def test_agreeing_sources_stay_grounded(conflict_app):
    client, sink_path, _ = conflict_app(reply=AGREE)
    answer = _chat(client)["structured"]["answer"]
    assert answer["status"] == "grounded"
    assert answer["conflict"]["disagreement"] is False
    assert _chat_event(sink_path)["conflict_disagreement"] is False


@pytest.mark.parametrize("reply,error", [("not json at all", None), (None, RuntimeError("LLM down"))])
def test_check_failure_leaves_status_unchanged(conflict_app, reply, error):
    client, sink_path, _ = conflict_app(reply=reply, error=error)
    answer = _chat(client)["structured"]["answer"]
    assert answer["status"] == "grounded"
    assert answer["conflict"] is None
    assert "conflict_disagreement" not in _chat_event(sink_path)


def test_off_skips_the_model_call(conflict_app):
    client, _, fake = conflict_app(mode="off")
    assert _chat(client)["structured"]["answer"]["status"] == "grounded"
    assert fake.calls == 0


def test_withheld_answer_skips_the_check_and_stays_insufficient(conflict_app):
    client, _, fake = conflict_app(answer="The claim has been paid in full.")
    answer = _chat(client)["structured"]["answer"]
    assert answer["guard"]["action"] == "withheld"
    assert answer["status"] == "insufficient_evidence"
    assert fake.calls == 0


def test_stream_final_event_carries_the_conflict_status(conflict_app):
    client, _, _ = conflict_app()
    response = client.post("/api/chat/stream", json={"query": "What is the mechanical labor cap?", "engine": "simulated"})
    frames = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    assert frames[-1]["structured"]["answer"]["status"] == "conflicting_evidence"
