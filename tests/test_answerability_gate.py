"""ANSWERABILITY_GATE wiring in AgenticRAGRouter._run_online_agent (no model loads)."""

from types import SimpleNamespace

from backend.agentic_router import AgenticRAGRouter
from tests.test_agentic_router_llm import FakeEmbedder, FakeVectorStore, RecordingClient


class JudgingClient(RecordingClient):
    def __init__(self, p_yes):
        super().__init__()
        self.p_yes = p_yes
        self.judged = []

    def yes_probability(self, messages, *, model, stage):
        self.judged.append({"messages": messages, "stage": stage})
        if isinstance(self.p_yes, Exception):
            raise self.p_yes
        return self.p_yes


def _caps(gate="llm", threshold=0.5):
    return SimpleNamespace(answerability_gate=gate, answerability_threshold=threshold)


def _run(client, caps, query="what is the labor cap?", claim_id=None):
    return AgenticRAGRouter().run_query(
        query_text=query, claim_id=claim_id, engine="lm-studio", embedding_engine=FakeEmbedder(),
        vector_store=FakeVectorStore(), reranking_engine=None, llm_client=client, caps=caps,
    )


def test_gate_refuses_below_threshold_without_synthesis():
    client = JudgingClient(0.02)
    result = _run(client, _caps())
    assert client.synthesis == [] and len(client.judged) == 1
    assert result["sources"] == []  # -> structured status insufficient_evidence
    assert result["answerability"]["p_yes"] == 0.02
    assert result["answerability"]["judged"] == ["labor.txt", "labor2.txt"]
    assert client.judged[0]["stage"] == "answerability_check"
    assert any("Answerability judge" in line for line in result["pipeline_logs"])


def test_gate_passes_an_answerable_question_through_to_synthesis():
    client = JudgingClient(0.97)
    result = _run(client, _caps())
    assert len(client.synthesis) == 1 and result["answer"] == "The Nevada mechanical cap is $110/hr."
    assert "answerability" not in result


def test_gate_off_makes_no_judge_call():
    for caps in (_caps(gate="off"), None):
        client = JudgingClient(0.0)
        result = _run(client, caps)
        assert client.judged == [] and len(client.synthesis) == 1 and result["sources"]


def test_gate_fails_open_when_the_judge_call_fails():
    client = JudgingClient(RuntimeError("down"))
    result = _run(client, _caps())
    assert len(client.synthesis) == 1 and result["sources"]
    assert any("Judge unavailable" in line for line in result["pipeline_logs"])


def test_gate_skips_computed_tool_audits():
    # Checklist/triage answers combine the claim record with SOPs through claims_tools; the
    # probe's judge scored two of them ~0, so the gate must never refuse them.
    client = JudgingClient(0.0)
    query = ("Run the SIU indicator checklist for claim #2026-30291. Screen the Fraud Red Flags SOP's "
             "indicators and say whether a referral to SIU is indicated.")
    result = _run(client, _caps(), query=query, claim_id="#2026-30291")
    assert client.judged == [] and len(client.synthesis) == 1
    assert result.get("tool_section")
