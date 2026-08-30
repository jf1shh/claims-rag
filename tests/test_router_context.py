from backend.agentic_router import AgenticRAGRouter


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
