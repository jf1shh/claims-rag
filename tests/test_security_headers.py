"""Audit finding E: the production surface must not serve interactive docs,
and every response must carry the framing/CSP/referrer headers, with HSTS
added only in production."""

from fastapi.testclient import TestClient

from app_factory import create_app
from config import Settings


def _settings(tmp_path, **overrides):
    env = {
        "RAG_DB_PATH": str(tmp_path / "rag.db"),
        "STORED_DOCUMENTS_DIR": str(tmp_path / "documents"),
        "JOBS_DB_PATH": str(tmp_path / "jobs.db"),
        "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
        **overrides,
    }
    return Settings.from_env(env)


def _production_settings(tmp_path):
    accounts_file = tmp_path / "service_accounts.json"
    accounts_file.write_text(
        '{"sa_key": {"subject": "worker", "tenant_id": "tenant-a", "roles": ["admin"]}}'
    )
    return _settings(
        tmp_path,
        APP_ENV="production",
        SIMULATION_MODE="false",
        AUTH_PROVIDERS="service-accounts",
        SERVICE_ACCOUNTS_FILE=str(accounts_file),
        CORS_ORIGINS="https://app.example.com",
        TENANT_ID="tenant-a",
    )


def test_given_development_settings_when_requested_then_docs_are_served(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_given_production_settings_when_requested_then_docs_are_disabled(tmp_path):
    client = TestClient(create_app(_production_settings(tmp_path)))
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_given_development_settings_when_responding_then_frame_and_csp_headers_are_set_but_not_hsts(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))
    response = client.get("/health/live")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in response.headers


def test_given_production_settings_when_responding_then_hsts_header_is_also_set(tmp_path):
    client = TestClient(create_app(_production_settings(tmp_path)))
    response = client.get("/health/live")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
