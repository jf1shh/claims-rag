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
