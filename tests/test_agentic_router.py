"""Unit tests for backend/agentic_router.py -- planner-output validation,
the engine allowlist, and claim-dossier markdown. No ML/network required:
the LLM transport is stubbed via a fake ChatClient."""
import pytest

from backend.agentic_router import CLAIMS_DATA, AgenticRAGRouter
from backend.llm_client import ChatClientError


@pytest.fixture
def router():
    return AgenticRAGRouter()


class _StubClient:
    """Deterministic fake ChatClient for the planner/allowlist tests."""
    def __init__(self, text):
        self._text = text
        self.calls = []

    def models(self):
        return ["m"]

    def model_for_stage(self, stage):
        return "m"

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        self.calls.append(True)
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


def _plan_for(router, llm_text, claim_id="#2026-30291"):
    client = _StubClient(llm_text)
    plan = router._get_llm_plan("test query", claim_id, client, "m")
    return plan, client


# ---------------------------------------------------------------------------
# Planner-plan validation -- the plan comes from a small local model, so
# valid-JSON-wrong-shape output is common and must never crash /api/chat
# ---------------------------------------------------------------------------

class TestPlanValidation:
    WELL_FORMED = ('{"needs_global_policies": false, "needs_claim_dossier": true, '
                   '"sub_queries": ["labor rates", "claim estimate"]}')

    MALFORMED = [
        '{"needs_global_policies": true}',        # missing sub_queries
        '{"sub_queries": "not a list"}',          # wrong type
        '{"sub_queries": []}',                    # empty list
        '{"sub_queries": [42, null]}',            # non-string items
        "[1, 2, 3]",                              # valid JSON, not a dict
        "not json at all",                        # parse failure
    ]

    def _assert_valid_shape(self, plan):
        assert set(plan) == {"needs_global_policies", "needs_claim_dossier", "sub_queries"}
        assert isinstance(plan["needs_global_policies"], bool)
        assert isinstance(plan["needs_claim_dossier"], bool)
        assert isinstance(plan["sub_queries"], list) and plan["sub_queries"]
        assert all(isinstance(q, str) and q.strip() for q in plan["sub_queries"])

    def test_well_formed_plan_is_respected(self, router):
        plan, _ = _plan_for(router, self.WELL_FORMED)
        self._assert_valid_shape(plan)
        assert plan["needs_global_policies"] is False
        assert plan["sub_queries"] == ["labor rates", "claim estimate"]

    @pytest.mark.parametrize("llm_text", MALFORMED)
    def test_malformed_plans_coerce_to_safe_shape(self, router, llm_text):
        plan, _ = _plan_for(router, llm_text)
        self._assert_valid_shape(plan)
        assert plan["sub_queries"] == ["test query"]

    def test_sub_queries_capped_at_three(self, router):
        plan, _ = _plan_for(router, '{"sub_queries": ["a", "b", "c", "d", "e"]}')
        assert plan["sub_queries"] == ["a", "b", "c"]

    def test_markdown_fenced_json_is_parsed(self, router):
        plan, _ = _plan_for(router, "```json\n" + self.WELL_FORMED + "\n```")
        assert plan["sub_queries"] == ["labor rates", "claim estimate"]

    def test_llm_unreachable_falls_back(self, router):
        plan, client = _plan_for(router, ChatClientError("down"), claim_id=None)
        self._assert_valid_shape(plan)
        assert len(client.calls) == 1
        assert plan["needs_claim_dossier"] is False  # no claim_id


# ---------------------------------------------------------------------------
# Engine allowlist -- regression for the SSRF/context-exfiltration vector:
# any engine value other than "simulated"/"lm-studio" must be rejected
# outright, never used as a request URL
# ---------------------------------------------------------------------------

class TestEngineAllowlist:
    @pytest.mark.parametrize("bad_engine", [
        "http://attacker.example/collect",
        "https://127.0.0.1:9999",
        "ollama",
        "",
    ])
    def test_unknown_engine_rejected_without_any_request(self, router, bad_engine):
        client = _StubClient("should not be called")
        # None for the engine/store args proves rejection happens before
        # any retrieval or network activity could occur.
        result = router.run_query("q", None, bad_engine, None, None, None, llm_client=client)
        assert client.calls == []
        assert result["sources"] == []
        assert "Unknown engine" in result["answer"]


# ---------------------------------------------------------------------------
# Claim dossier markdown
# ---------------------------------------------------------------------------

class TestClaimContextMarkdown:
    def test_known_claim_renders_full_dossier(self, router):
        claim = CLAIMS_DATA[0]
        md = router._get_claim_context_markdown(claim["id"])
        assert claim["id"] in md
        assert claim["insured"] in md
        # Every estimate line item must appear -- the multi-line-item
        # enumeration fix (Phase 9) depends on the LLM seeing all of them.
        for row in claim["estimate"]:
            assert row["op"] in md
            assert row["total"] in md

    def test_unknown_claim_renders_empty(self, router):
        assert router._get_claim_context_markdown("#0000-00000") == ""
