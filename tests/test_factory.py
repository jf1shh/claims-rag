from config import Settings
from app_factory import _build_llm_client, _build_reranker
from backend.reranker import FallbackReranker, LocalReranker, RemoteReranker


def test_build_llm_client_returns_none_for_none_provider():
    s = Settings.from_env({"LLM_PROVIDER": "none"})
    assert _build_llm_client(s) is None


def test_build_llm_client_uses_stage_models_from_settings():
    s = Settings.from_env({"PLANNING_MODEL": "p", "SYNTHESIS_MODEL": "sy", "LLM_BASE_URL": "http://gw"})
    client = _build_llm_client(s)
    assert client is not None
    assert client.base_url == "http://gw"
    assert client.model_for_stage("planning") == "p"
    assert client.model_for_stage("synthesis") == "sy"


def test_build_reranker_local_returns_local():
    assert isinstance(_build_reranker(Settings.from_env({})), LocalReranker)


def test_build_reranker_wires_max_concurrency_from_settings():
    reranker = _build_reranker(Settings.from_env({"RERANK_MAX_CONCURRENCY": "5"}))
    assert reranker.max_concurrency == 5



def test_build_reranker_wires_batching_settings_from_environment():
    settings = Settings.from_env({
        "RERANK_BATCHING_ENABLED": "false",
        "RERANK_BATCH_MAX_WAIT_MS": "8",
        "RERANK_BATCH_MAX_REQUESTS": "4",
        "RERANK_BATCH_MAX_PAIRS": "64",
        "RERANK_INFERENCE_BATCH_SIZE": "8",
        "RERANK_BATCH_MAX_PENDING": "32",
        "RERANK_CANDIDATE_POOL": "50",
    })
    reranker = _build_reranker(settings)
    assert reranker.batching_enabled is False
    assert reranker.batch_max_wait_ms == 8
    assert reranker.batch_max_requests == 4
    assert reranker.batch_max_pairs == 64
    assert reranker.inference_batch_size == 8
    assert reranker.batch_max_pending == 32
    assert _build_reranker(Settings.from_env({})).device == "auto"
    assert _build_reranker(Settings.from_env({"RERANK_DEVICE": "cpu"})).device == "cpu"


def test_build_reranker_remote_wraps_remote_with_local_fallback():
    reranker = _build_reranker(Settings.from_env({"RERANK_PROVIDER": "remote", "RERANK_ENDPOINT": "http://rr"}))
    assert isinstance(reranker, FallbackReranker)
    assert isinstance(reranker._primary, RemoteReranker)
    assert isinstance(reranker._fallback, LocalReranker)


def test_app_dependencies_expose_llm_client():
    from app_factory import build_dependencies
    deps = build_dependencies(Settings.from_env({"LLM_PROVIDER": "none"}))
    assert deps.llm_client is None
