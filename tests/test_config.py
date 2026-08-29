import pytest

from config import Settings


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
