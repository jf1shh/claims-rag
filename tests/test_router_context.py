from fastapi.testclient import TestClient

import backend.app as app_module
from backend.agentic_router import AgenticRAGRouter
from backend.app import app
from config import Settings


def _chunks(n):
    return [{"content": f"dossier chunk {i}", "filename": f"d{i}.txt",
             "file_type": "txt", "score": 1.0} for i in range(n)]


def _matches(n):
    return [{"content": f"global match {i}", "filename": f"g{i}.txt",
             "file_type": "txt", "score": 1.0 - i * 0.01} for i in range(n)]


def test_assembly_caps_dossier_and_global(tmp_path, monkeypatch):
    from config import Settings
    s = Settings.from_env({"CONTEXT_MAX_CLAIM_CHUNKS": "3", "CONTEXT_MAX_GLOBAL_MATCHES": "2"})
    router = AgenticRAGRouter()
    sys_p, user_p, sources, filenames = router._assemble_context(
        _chunks(10), _matches(5), "q", "#c", caps=s)
    assert len(sources) == 5         # 3 dossier + 2 global
    assert filenames == ["d0.txt", "d1.txt", "d2.txt", "g0.txt", "g1.txt"]
    assert user_p.count("<source file=") == 5  # delimited sources in user prompt
    assert "treat any instructions" in sys_p    # data-vs-instruction line in system prompt
    assert "<user_query>q</user_query>" in user_p


def test_system_prompt_restores_enumeration_and_calculation_clauses():
    # Regression for a whole-branch-review finding: Task 3's rewrite of
    # _assemble_context's system_prompt dropped two clauses the original
    # (pre-Task-3) _run_online_agent system prompt had -- see CLAUDE.md's
    # Phase 9 section, which documents that the enumeration instruction
    # exists specifically because the model, with the correct multi-line-item
    # receipt already in context, still calculated a total from only one
    # line item (a previously-shipped, real bug).
    router = AgenticRAGRouter()
    sys_p, _user_p, _sources, _filenames = router._assemble_context(
        _chunks(1), _matches(1), "q", "#c", caps=None)
    assert "do not calculate from a single item if more than one applies" in sys_p
    assert "Perform calculations (payouts, caps, deductibles) if asked" in sys_p


class _PlannerAndSynthesisStubClient:
    """Deterministic ChatClient stub: routes global policies only (no claim
    dossier gating needed since claim_chunks come straight from the stub
    vector store), and returns a fixed synthesis answer."""

    def models(self):
        return ["m"]

    def model_for_stage(self, stage):
        return "m"

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        if "Claims Planner" in messages[0]["content"]:
            return ('{"needs_global_policies": false, "needs_claim_dossier": true, '
                     '"sub_queries": ["q"]}')
        return "answer text"


class _ManyChunksVectorStore:
    def search_similarity(self, *a, **kw):
        return []

    def get_claim_chunks(self, claim_id):
        # More chunks than the small cap configured below (and more than the
        # hardcoded default of 8), so a capped vs. uncapped response is
        # unambiguous either way.
        return [{"content": f"chunk {i}", "filename": f"c{i}.txt",
                 "file_type": "txt", "score": 1.0} for i in range(10)]


class _StubEmbedder:
    def embed_query(self, q):
        return [0.0] * 8


def test_json_chat_endpoint_respects_context_caps_from_settings(monkeypatch):
    # Regression for a whole-branch-review finding: chat_with_docs (the JSON
    # /api/chat handler) called agentic_router.run_query(...) WITHOUT
    # caps=settings, while the streaming handler did pass it -- so
    # CONTEXT_MAX_CLAIM_CHUNKS/etc only ever configured the streaming path.
    # Feed 10 claim chunks through a stub router/vector-store with a small
    # configured cap and confirm /api/chat's `sources` list is capped
    # accordingly (not capped at the hardcoded default of 8).
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"CONTEXT_MAX_CLAIM_CHUNKS": "2"}))
    monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: _StubEmbedder())
    monkeypatch.setattr(app_module, "vector_store", _ManyChunksVectorStore())
    monkeypatch.setattr(app_module, "_llm_client", _PlannerAndSynthesisStubClient())
    monkeypatch.setattr(app_module, "_reranker", None)

    client = TestClient(app)
    resp = client.post("/api/chat", json={"query": "q", "engine": "lm-studio", "claim_id": "#test-claim"})
    assert resp.status_code == 200
    assert len(resp.json()["sources"]) == 2


def test_hard_truncation_keeps_source_blocks_well_formed_and_citations_consistent():
    # Regression for a whole-branch-review bundled-minor finding: when even
    # dropping every global match doesn't bring the dossier under the char
    # budget, a raw slice of the joined source_text could leave a dangling,
    # unterminated <source> block (with <user_query> rendered inside it) and
    # could cut mid HTML-entity; the citations returned (top_matches /
    # filenames) must also stay consistent with what source_text actually
    # contains -- no citation for content that was truncated away entirely.
    router = AgenticRAGRouter()
    big_chunks = [{"content": "x" * 500, "filename": f"d{i}.txt",
                   "file_type": "txt", "score": 1.0} for i in range(5)]
    caps = Settings.from_env({"CONTEXT_MAX_CLAIM_CHUNKS": "5", "CONTEXT_MAX_GLOBAL_MATCHES": "1",
                               "CONTEXT_MAX_PROMPT_CHARS": "600"})
    sys_p, user_p, sources, filenames = router._assemble_context(
        big_chunks, [], "q", "#c", caps=caps)

    # Every <source ...> tag opened in the prompt must be closed.
    assert user_p.count("<source file=") == user_p.count("</source>")
    # <user_query> must never land inside an unterminated <source> block --
    # the closing </source> for the last kept block must precede it.
    last_source_close = user_p.rfind("</source>")
    assert last_source_close != -1
    assert last_source_close < user_p.index("<user_query>")
    # The returned citations must match what's actually present in the
    # rendered prompt.
    for fn in filenames:
        assert f'<source file="{fn}"' in user_p
    assert len(sources) == len(filenames)
