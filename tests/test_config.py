import pytest

from config import Settings


def test_given_remote_reranker_then_settings_are_parsed():
    settings = Settings.from_env({
        "RERANK_PROVIDER": "remote",
        "RERANK_ENDPOINT": "https://reranker.internal",
        "RERANK_CANDIDATE_POOL": "75",
        "RERANK_TIMEOUT_SECONDS": "7",
        "RERANK_API_KEY": "secret",
    })
    assert settings.rerank_provider == "remote"
    assert settings.rerank_endpoint == "https://reranker.internal"
    assert settings.rerank_candidate_pool == 75
    assert settings.rerank_timeout_seconds == 7
    assert settings.rerank_api_key == "secret"


def test_given_remote_reranker_without_endpoint_then_validation_fails():
    settings = Settings.from_env({"RERANK_PROVIDER": "remote"})
    with pytest.raises(ValueError, match="RERANK_ENDPOINT"):
        settings.validate_for_environment()


def test_given_invalid_reranker_provider_then_validation_fails():
    settings = Settings.from_env({"RERANK_PROVIDER": "cpu"})
    with pytest.raises(ValueError, match="RERANK_PROVIDER"):
        settings.validate_for_environment()


def test_given_empty_development_environment_then_local_defaults_are_explicit():
    settings = Settings.from_env({"APP_ENV": "development"})
    assert settings.app_env == "development"
    assert settings.simulation_mode is True
    assert settings.max_top_k > 0
    assert settings.rag_db_path.name == "rag_store.db"


def test_given_production_without_required_provider_configuration_then_validation_fails():
    settings = Settings.from_env(
        {
            "APP_ENV": "production",
            "LLM_PROVIDER": "openai-compatible",
            "SIMULATION_MODE": "false",
            "CORS_ORIGINS": "https://claims.example.com",
            "LLM_BASE_URL": "",
            # A real auth provider, so validation reaches the LLM check rather
            # than failing first on the development-provider-in-production rule.
            "AUTH_PROVIDERS": "oidc",
            "OIDC_ISSUER": "https://idp.example.com",
            "OIDC_CLIENT_ID": "app-1",
        }
    )
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        settings.validate_for_environment()


def test_given_malformed_cors_origins_then_validation_fails():
    settings = Settings.from_env(
        {"APP_ENV": "production", "SIMULATION_MODE": "false", "CORS_ORIGINS": "not-a-url"}
    )
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        settings.validate_for_environment()


def test_given_invalid_integer_setting_then_parsing_fails():
    with pytest.raises(ValueError, match="MAX_TOP_K"):
        Settings.from_env({"MAX_TOP_K": "zero"})


def test_given_invalid_ingestion_mode_then_validation_fails():
    settings = Settings.from_env({"INGESTION_MODE": "batch"})
    with pytest.raises(ValueError, match="INGESTION_MODE"):
        settings.validate_for_environment()


def test_given_async_ingestion_mode_then_parsed():
    settings = Settings.from_env({"INGESTION_MODE": "async"})
    assert settings.ingestion_mode == "async"
    assert settings.jobs_db_path.name == "jobs.db"


def test_given_sqs_queue_provider_without_url_then_validation_fails():
    settings = Settings.from_env({"QUEUE_PROVIDER": "sqs"})
    with pytest.raises(ValueError, match="SQS_QUEUE_URL"):
        settings.validate_for_environment()


def test_given_auth_providers_when_parsed_then_comma_separated_and_lowercased():
    settings = Settings.from_env({"AUTH_PROVIDERS": "OIDC, service-accounts"})
    assert settings.auth_providers == ("oidc", "service-accounts")


def test_given_unknown_auth_provider_then_validation_fails():
    settings = Settings.from_env({"AUTH_PROVIDERS": "magic"})
    with pytest.raises(ValueError, match="AUTH_PROVIDERS"):
        settings.validate_for_environment()


def test_given_empty_auth_providers_then_validation_fails():
    settings = Settings.from_env({"AUTH_PROVIDERS": ""})
    with pytest.raises(ValueError, match="AUTH_PROVIDERS"):
        settings.validate_for_environment()


def test_given_oidc_without_issuer_then_validation_fails():
    settings = Settings.from_env({"AUTH_PROVIDERS": "oidc", "OIDC_CLIENT_ID": "app-1"})
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        settings.validate_for_environment()


def test_given_oidc_without_client_id_then_validation_fails():
    settings = Settings.from_env({"AUTH_PROVIDERS": "oidc", "OIDC_ISSUER": "https://idp.example.com"})
    with pytest.raises(ValueError, match="OIDC_CLIENT_ID"):
        settings.validate_for_environment()


def test_given_service_accounts_without_file_then_validation_fails():
    settings = Settings.from_env({"AUTH_PROVIDERS": "service-accounts"})
    with pytest.raises(ValueError, match="SERVICE_ACCOUNTS_FILE"):
        settings.validate_for_environment()


def test_given_production_with_development_auth_then_validation_fails():
    settings = Settings.from_env(
        {
            "APP_ENV": "production",
            "SIMULATION_MODE": "false",
            "CORS_ORIGINS": "https://claims.example.com",
            "AUTH_PROVIDERS": "development",
        }
    )
    with pytest.raises(ValueError, match="AUTH_PROVIDERS"):
        settings.validate_for_environment()


def test_given_production_with_oidc_auth_then_validation_passes():
    settings = Settings.from_env(
        {
            "APP_ENV": "production",
            "SIMULATION_MODE": "false",
            "CORS_ORIGINS": "https://claims.example.com",
            "AUTH_PROVIDERS": "oidc",
            "OIDC_ISSUER": "https://idp.example.com",
            "OIDC_CLIENT_ID": "app-1",
        }
    )
    settings.validate_for_environment()
    assert settings.auth_providers == ("oidc",)


def test_given_claim_acls_file_when_parsed_then_path_is_configured():
    settings = Settings.from_env({"CLAIM_ACLS_FILE": "./claim-acls.json"})
    assert settings.claim_acls_file is not None
    assert settings.claim_acls_file.name == "claim-acls.json"


def test_given_no_claim_acls_file_then_open_policy_is_the_default():
    settings = Settings.from_env({"APP_ENV": "development"})
    assert settings.claim_acls_file is None


def test_given_audit_log_path_when_parsed_then_path_is_configured():
    settings = Settings.from_env({"AUDIT_LOG_PATH": "./logs/audit.jsonl"})
    assert settings.audit_log_path.name == "audit.jsonl"
    assert settings.audit_log_path.parent.name == "logs"


def test_given_no_audit_log_path_then_repo_root_default_is_used():
    settings = Settings.from_env({"APP_ENV": "development"})
    assert settings.audit_log_path.name == "audit.log.jsonl"


def test_given_rate_limit_settings_when_parsed_then_values_are_configured():
    settings = Settings.from_env({"RATE_LIMIT_MAX_REQUESTS": "25", "RATE_LIMIT_WINDOW_SECONDS": "5"})
    assert settings.rate_limit_max_requests == 25
    assert settings.rate_limit_window_seconds == 5.0


def test_given_no_rate_limit_settings_then_sane_defaults_are_used():
    settings = Settings.from_env({"APP_ENV": "development"})
    assert settings.rate_limit_max_requests == 60
    assert settings.rate_limit_window_seconds == 60.0


def test_given_nonpositive_rate_limit_then_parsing_fails():
    with pytest.raises(ValueError, match="RATE_LIMIT_MAX_REQUESTS"):
        Settings.from_env({"RATE_LIMIT_MAX_REQUESTS": "0"})
    with pytest.raises(ValueError, match="RATE_LIMIT_WINDOW_SECONDS"):
        Settings.from_env({"RATE_LIMIT_WINDOW_SECONDS": "-1"})


def test_given_stage_models_when_parsed_then_catalog_is_configured():
    s = Settings.from_env({
        "PLANNING_MODEL": "fast-plan",
        "SYNTHESIS_MODEL": "domain-v2",
        "EVAL_MODEL": "judge-1",
        "LLM_API_KEY": "sk-test",
        "LLM_PLAN_TIMEOUT_SECONDS": "3",
        "LLM_SYNTHESIS_TIMEOUT_SECONDS": "90",
        "MODEL_CACHE_TTL_SECONDS": "20",
    })
    assert s.planning_model == "fast-plan"
    assert s.synthesis_model == "domain-v2"
    assert s.eval_model == "judge-1"
    assert s.llm_api_key == "sk-test"
    assert s.llm_plan_timeout_seconds == 3
    assert s.llm_synthesis_timeout_seconds == 90
    assert s.model_cache_ttl_seconds == 20


def test_given_no_stage_models_then_defaults_fall_back_to_single_model():
    s = Settings.from_env({"LLM_MODEL": "fallback-model"})
    assert s.planning_model is None
    assert s.synthesis_model is None
    assert s.eval_model is None
    assert s.llm_api_key is None
    assert s.llm_synthesis_timeout_seconds == 120
    assert s.llm_plan_timeout_seconds == 5
    assert s.model_cache_ttl_seconds == 10


def test_given_context_caps_when_parsed_then_configured():
    s = Settings.from_env({
        "CONTEXT_MAX_CLAIM_CHUNKS": "6",
        "CONTEXT_MAX_GLOBAL_MATCHES": "3",
        "CONTEXT_MAX_PROMPT_CHARS": "50000",
    })
    assert s.context_max_claim_chunks == 6
    assert s.context_max_global_matches == 3
    assert s.context_max_prompt_chars == 50000


def test_given_no_context_caps_then_defaults_used():
    s = Settings.from_env({"APP_ENV": "development"})
    assert s.context_max_claim_chunks == 8
    assert s.context_max_global_matches == 4
    assert s.context_max_prompt_chars == 60000
