from fastapi.testclient import TestClient

from app_factory import create_app
from config import Settings


def test_given_development_settings_when_app_is_created_then_live_health_does_not_load_models(tmp_path):
    settings = Settings.from_env(
        {
            "APP_ENV": "development",
            "SIMULATION_MODE": "true",
            "RAG_DB_PATH": str(tmp_path / "rag.db"),
            "STORED_DOCUMENTS_DIR": str(tmp_path / "documents"),
        }
    )
    client = TestClient(create_app(settings))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_given_usable_local_store_when_readiness_is_requested_then_ready_is_returned(tmp_path):
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "RAG_DB_PATH": str(tmp_path / "rag.db"),
            "STORED_DOCUMENTS_DIR": str(tmp_path / "documents"),
        }
    )
    client = TestClient(create_app(settings))
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "failed_dependencies": {}}
