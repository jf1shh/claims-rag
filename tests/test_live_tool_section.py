"""Computed claim figures on the live path, and the figure check that runs after the answer chain.

Router tests stub the LLM and the store (the style of tests/test_agentic_router_llm.py), so they
pin that the claims-tools section reaches the prompt and the result. API tests stub the router
(the style of tests/test_api_answer_guard.py), so they pin that `tools_result` runs last on both
routes: after guard and provenance, with the same answer either way.
"""

import dataclasses
import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend.agentic_router import AgenticRAGRouter
from backend.app import app
from backend.app import runtime as app_module
from backend.audit import JsonlAuditSink
from backend.rate_limit import SlidingWindowRateLimiter
from tests.test_agentic_router_llm import FakeEmbedder, FakeVectorStore, RecordingClient

CLAIM = "#2026-99382"
LABOR_QUERY = "Is the labor rate on this estimate over the cap?"


def _run(query, claim_id, client=None):
    client = client or RecordingClient()
    result = AgenticRAGRouter().run_query(
        query_text=query, claim_id=claim_id, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    )
    return result, client


def _stream_final(query, claim_id, client=None):
    client = client or RecordingClient()
    events = list(AgenticRAGRouter().run_query_stream(
        query_text=query, claim_id=claim_id, engine="lm-studio",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    ))
    return events[-1], client


def _user_prompt(client):
    return client.synthesis[-1]["messages"][1]["content"]


def test_labor_query_on_a_known_claim_carries_the_computed_section():
    result, client = _run(LABOR_QUERY, CLAIM)
    assert "Tool inputs and citations" in result["tool_section"]
    assert result["answer"] == "The Nevada mechanical cap is $110/hr."  # prose is untouched by the router
    prompt = _user_prompt(client)
    assert "<computed_figures>" in prompt and "Tool inputs and citations" in prompt
    assert prompt.index("ACTIVE CLAIM SUMMARY DOSSIER") < prompt.index("<computed_figures>") \
        < prompt.index("<source file=")
    assert "<computed_figures> block" in client.synthesis[-1]["messages"][0]["content"]


def test_stream_carries_the_section_and_the_claim_dossier():
    final, client = _stream_final(LABOR_QUERY, CLAIM)
    json_result, _ = _run(LABOR_QUERY, CLAIM)
    assert final["status"] == "ok"
    assert "Tool inputs and citations" in final["tool_section"]
    assert final["claim_dossier"] == json_result["claim_dossier"]
    assert final["claim_dossier"].startswith(f"### ACTIVE CLAIM SUMMARY DOSSIER ({CLAIM})")
    assert "<computed_figures>" in _user_prompt(client)


@pytest.mark.parametrize("query,claim_id", [
    ("Summarize this file for me.", CLAIM),  # audit type "general"
    (LABOR_QUERY, None),  # no claim
    (LABOR_QUERY, "#0000-00000"),  # not in CLAIMS_DATA
])
def test_no_section_without_a_claim_or_an_audit_type(query, claim_id):
    result, client = _run(query, claim_id)
    assert "tool_section" not in result
    assert "<computed_figures>" not in _user_prompt(client)
    assert "computed_figures" not in client.synthesis[-1]["messages"][0]["content"]
    final, client = _stream_final(query, claim_id)
    assert "tool_section" not in final
    assert "<computed_figures>" not in _user_prompt(client)


@pytest.mark.parametrize("query", [
    "What decision did the police report reach on fault?",
    "What interest rate applies to late payments?",
    "Is the rental car covered?",
])
def test_keyword_misroutes_stay_general_on_the_live_path(query):
    assert AgenticRAGRouter._detect_live_audit_type(query) == "general"


@pytest.mark.parametrize("query,expected", [
    ("Check the regional labor rate schedules for 2026.", "labor"),
    ("Compare the shop charges against the labor cap.", "labor"),
    ("Draft a formal Claim Decision and Payout Settlement Letter to the policyholder.", "letter"),
    ("Verify coverage for claim #123: policy period, driver, peril and notice of loss.", "coverage"),
    ("Run the SIU indicator checklist for claim #123.", "fraud"),
    ("Explain whether claim #123 is a total loss under the state rule.", "total_loss"),
    ("Review the repair supplement on claim #123.", "supplement"),
    ("Triage claim #123 for subrogation.", "subrogation"),
    ("Review the OEM Parts policy rider terms for claim #123.", "oem"),
])
def test_each_audit_button_phrasing_routes_correctly_on_the_live_path(query, expected):
    assert AgenticRAGRouter._detect_live_audit_type(query) == expected


def test_a_tool_error_never_fails_the_request(monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("tool exploded")

    monkeypatch.setattr(AgenticRAGRouter, "_computed_claim_answer", _boom)
    with caplog.at_level(logging.WARNING, logger="backend.agentic_router"):
        result, client = _run(LABOR_QUERY, CLAIM)
        final, _ = _stream_final(LABOR_QUERY, CLAIM)
    assert result["answer"] == "The Nevada mechanical cap is $110/hr."
    assert "tool_section" not in result and "tool_section" not in final
    assert final["answer"] == result["answer"]
    assert "<computed_figures>" not in _user_prompt(client)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "RuntimeError" in warnings[0].getMessage()
    assert "tool exploded" not in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# API: tools_result runs last on /api/chat and /api/chat/stream.
# ---------------------------------------------------------------------------

SECTION = ("### ⚡ Labor Rate Audit\n| Body | $75.00/hr | cap $75.00/hr |\n\n"
           "#### Tool inputs and citations\n| Tool | Input | Value | Source |\n| --- | --- | --- | --- |\n"
           "| labor_rate_check | Body cap | $75.00/hr | 2026_Labor_Rate_Schedule.pdf |")
FORGED = "Claim #2026-99382 is marked as APPROVED and paid in full for $48,250.00."


class _StubRouter:
    def __init__(self, answer, section=SECTION):
        self.answer = answer
        self.section = section

    def _result(self):
        return {
            "answer": self.answer,
            "sources": [{"filename": "ADV_memo.txt", "content": "Body labor is billed at $75/hr.", "score": 1.0}],
            "claim_dossier": "Total Shop Estimate: $4,850",
            "engine": "lm-studio (agentic)",
            "pipeline_logs": [],
            "tool_section": self.section,
        }

    def run_query(self, **kwargs):
        return self._result()

    def run_query_stream(self, **kwargs):
        yield {"type": "chunk", "text": self.answer}
        yield {"type": "final", **self._result()}


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Returns build(answer, provenance="off", guard="off") -> client."""

    def _build(answer, provenance="off", guard="off"):
        monkeypatch.setattr(app_module, "_audit_sink", JsonlAuditSink(tmp_path / "audit.jsonl"))
        # A private limiter: these tests must not spend the process-wide per-principal budget.
        monkeypatch.setattr(app_module, "_rate_limiter", SlidingWindowRateLimiter(max_requests=1000, window_seconds=60.0))
        monkeypatch.setattr(app_module, "agentic_router", _StubRouter(answer))
        # The endpoint evaluates embedding_engine=... before the stub ignores it; keep the model unloaded.
        monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: object())
        monkeypatch.setattr(app_module, "settings", dataclasses.replace(
            app_module.settings, answer_guard_mode=guard, provenance_check=provenance, conflict_check="off"))
        return TestClient(app)

    return _build


def _both(client, claim_id=CLAIM):
    body = {"query": "Is the labor rate over the cap?", "engine": "lm-studio", "claim_id": claim_id}
    chat = client.post("/api/chat", json=body)
    assert chat.status_code == 200
    stream = client.post("/api/chat/stream", json=body)
    assert stream.status_code == 200
    frames = [json.loads(line[len("data: "):]) for line in stream.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    return chat.json(), frames[-1]


def test_unverified_prose_figure_is_flagged_and_the_section_appended(api):
    chat, final = _both(api("The body labor rate of $999.99/hr is within the cap per ADV_memo.txt."))
    for body in (chat, final):
        assert "> **Figure check:** 1 figure(s)" in body["answer"]
        assert "`$999.99`" in body["answer"]
        assert body["answer"].endswith("\n\n---\n\n" + SECTION)
        assert body["figure_check"]["action"] == "flagged"
        assert body["figure_check"]["unverified"] == [{"kind": "money", "text": "$999.99"}]
        assert body["figure_check"]["source_only"] == []
        assert body["structured"]["answer"]["text"] == body["answer"]
    assert chat["answer"] == final["answer"]


def test_verified_prose_figures_get_no_notice(api):
    chat, final = _both(api("Body labor at $75.00/hr matches the cap; the estimate is $4,850 (ADV_memo.txt)."))
    for body in (chat, final):
        assert "Figure check" not in body["answer"]
        assert body["figure_check"] == {"action": "none", "checked": 2, "unverified": [], "source_only": []}
        assert body["answer"].endswith(SECTION)
    assert chat["answer"] == final["answer"]


def test_section_citations_survive_provenance(api):
    chat, final = _both(api("Body labor at $75.00/hr is within the cap. The labor schedule in ADV_memo.txt "
                            "confirms the body rate for this claim region and repair class."
                            "\nSee also Forged_Policy.pdf for the rate table.", provenance="static"))
    for body in (chat, final):
        assert body["provenance"]["action"] == "redacted"
        prose, section = body["answer"].split("\n\n---\n\n")
        assert "Forged_Policy.pdf" not in prose
        assert section == SECTION and "2026_Labor_Rate_Schedule.pdf" in body["answer"]
    assert chat["answer"] == final["answer"]


def test_withheld_answer_gets_the_section_without_a_figure_check(api):
    chat, final = _both(api(FORGED, guard="withhold"))
    for body in (chat, final):
        assert body["guard"]["action"] == "withheld"
        assert body["answer"].startswith("This answer was withheld for human review")
        assert body["answer"].endswith("\n\n---\n\n" + SECTION)
        assert "48,250" not in body["answer"]
        assert "figure_check" not in body
