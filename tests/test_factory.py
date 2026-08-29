from config import Settings
from app_factory import _build_llm_client, AppDependencies


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


def test_app_dependencies_expose_llm_client():
    from app_factory import build_dependencies
    deps = build_dependencies(Settings.from_env({"LLM_PROVIDER": "none"}))
    assert deps.llm_client is None