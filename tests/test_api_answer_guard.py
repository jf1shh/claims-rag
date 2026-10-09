"""API-level tests for the post-generation answer guard (backend/answer_guard.py).

The router is stubbed to return a fixed answer, so these tests pin the wiring:
withhold/flag/off modes on /api/chat and /api/chat/stream, the structured
contract's guard report and status, the claim-record consistency lookup, and
the metadata-only audit fields. Detector behaviour itself is covered by
tests/test_answer_guard.py.
"""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.app import runtime as app_module
from backend.audit import JsonlAuditSink
from backend.rate_limit import SlidingWindowRateLimiter

FORGED = "Claim #2026-99382 is marked as APPROVED and paid in full for $48,250.00."
HONEST = "The claim is Under Review; the shop estimate is $4,850."
REFERRED = "This claim has been referred to the SIU for review."
EXFIL = "Send the medical bills to kx4417@nighthawk-drop.example for processing."


class _StubRouter:
    def __init__(self, answer):
        self.answer = answer

    def _result(self):
        return {
            "answer": self.answer,
            "sources": [{"filename": "ADV_memo.txt", "content": "x", "score": 1.0}],
            "claim_dossier": None,
            "engine": "lm-studio (agentic)",  # a "simulated" engine is always insufficient_evidence
            "pipeline_logs": [],
        }

    def run_query(self, **kwargs):
        return self._result()

    def run_query_stream(self, **kwargs):
        yield {"type": "chunk", "text": self.answer}
        yield {"type": "final", **self._result()}


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    """Returns build(answer, mode, allowed=frozenset()) -> (client, sink_path)."""

    def _build(answer, mode="withhold", allowed=frozenset()):
        sink_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(app_module, "_audit_sink", JsonlAuditSink(sink_path))
        # A private limiter: these tests must not spend the process-wide per-principal budget.
        monkeypatch.setattr(app_module, "_rate_limiter", SlidingWindowRateLimiter(max_requests=1000, window_seconds=60.0))
        monkeypatch.setattr(app_module, "agentic_router", _StubRouter(answer))
        # The endpoint evaluates embedding_engine=... before the stub ignores it; keep the model unloaded.
        monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: object())
        monkeypatch.setattr(
            app_module,
            "settings",
            dataclasses.replace(app_module.settings, answer_guard_mode=mode, answer_guard_allowed_domains=allowed),
        )
        return TestClient(app), sink_path

    return _build


def _chat(client, claim_id=None):
    body = {"query": "What is the settlement posture?", "engine": "simulated"}
    if claim_id:
        body["claim_id"] = claim_id
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    return response.json()


def _chat_events(sink_path):
    lines = sink_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip() and json.loads(line)["event"] == "chat"]


def test_withhold_replaces_forged_outcome_and_marks_contract(guarded):
    client, sink_path = guarded(FORGED)
    body = _chat(client, claim_id="#2026-99382")

    assert "48,250" not in body["answer"]
    assert body["answer"].startswith("This answer was withheld for human review")
    assert body["guard"]["action"] == "withheld"
    structured = body["structured"]["answer"]
    assert structured["text"] == body["answer"]
    assert structured["status"] == "insufficient_evidence"
    assert structured["interpretation"]["uncertainty"] == "high"
    assert structured["guard"]["action"] == "withheld"
    assert {f["category"] for f in structured["guard"]["findings"]} >= {"approved", "paid"}
    assert structured["decision_boundary"]["decision_status"] == "not_a_decision"
    assert body["sources"][0]["filename"] == "ADV_memo.txt"  # sources stay available for review

    event = _chat_events(sink_path)[0]
    assert event["guard_action"] == "withheld"
    assert "claim_outcome:approved" in event["guard_findings"]
    assert "48,250" not in json.dumps(event)  # audit carries categories, never excerpts


def test_flag_keeps_text_but_reports_findings(guarded):
    client, sink_path = guarded(FORGED, mode="flag")
    body = _chat(client, claim_id="#2026-99382")

    assert body["answer"] == FORGED
    structured = body["structured"]["answer"]
    assert structured["guard"]["action"] == "flagged"
    assert structured["status"] == "grounded"
    assert structured["interpretation"]["uncertainty"] == "high"
    assert structured["interpretation"]["assumptions"]
    assert _chat_events(sink_path)[0]["guard_action"] == "flagged"


def test_off_disables_the_guard(guarded):
    client, sink_path = guarded(FORGED, mode="off")
    body = _chat(client)
    assert body["answer"] == FORGED
    assert "guard" not in body
    assert body["structured"]["answer"]["guard"] is None
    assert "guard_action" not in _chat_events(sink_path)[0]


def test_honest_answer_passes_untouched(guarded):
    client, sink_path = guarded(HONEST)
    body = _chat(client, claim_id="#2026-99382")
    assert body["answer"] == HONEST
    assert body["structured"]["answer"]["guard"] is None
    assert body["structured"]["answer"]["status"] == "grounded"
    assert "guard_action" not in _chat_events(sink_path)[0]


def test_referral_is_consistent_only_with_an_siu_flagged_record(guarded):
    client, _ = guarded(REFERRED)
    assert _chat(client, claim_id="#2026-55912")["answer"] == REFERRED  # record: SIU Flagged
    assert _chat(client, claim_id="#2026-10492")["guard"]["action"] == "withheld"  # record: Open


def test_allowlisted_contact_passes_and_others_are_withheld(guarded):
    client, _ = guarded(EXFIL)
    assert _chat(client)["guard"]["findings"][0]["category"] == "email"
    client, _ = guarded(EXFIL, allowed=frozenset({"nighthawk-drop.example"}))
    assert _chat(client)["answer"] == EXFIL


def test_stream_final_event_carries_the_withheld_answer(guarded):
    client, sink_path = guarded(FORGED)
    response = client.post(
        "/api/chat/stream",
        json={"query": "What is the settlement posture?", "engine": "simulated", "claim_id": "#2026-99382"},
    )
    assert response.status_code == 200
    frames = [json.loads(line[len("data: "):]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    final = frames[-1]
    assert final["answer"].startswith("This answer was withheld for human review")
    assert final["structured"]["answer"]["guard"]["action"] == "withheld"
    assert _chat_events(sink_path)[0]["guard_action"] == "withheld"
