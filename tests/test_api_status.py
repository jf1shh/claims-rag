from fastapi.testclient import TestClient
from backend.app import runtime as app_module
from backend.app import app
from backend.llm_client import OpenAICompatibleClient
from backend.tenant_context import PrincipalContext


def test_status_reports_gateway_models_via_client(monkeypatch):
    class FakeHTTP:
        def __init__(self): self.gets = []
        def post(self, *a, **k): raise NotImplementedError
        def get(self, url, timeout=None, **k):
            self.gets.append(url)
            class R:
                status_code = 200
                def json(self): return {"data": [{"id": "qwen2.5-14b"}]}
            return R()
    client = OpenAICompatibleClient(base_url="http://gw", default_model="M", http=FakeHTTP())
    monkeypatch.setattr(app_module, "_llm_client", client)
    monkeypatch.setattr(app_module, "settings", __import__("config").Settings.from_env({"LLM_PROVIDER": "lm-studio"}))
    resp = TestClient(app).get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["lm_studio"]["models"] == ["qwen2.5-14b"]


class _FixedAuthenticator:
    def __init__(self, principal):
        self._principal = principal

    def authenticate(self, request):
        return self._principal


def test_given_principal_without_documents_read_when_status_requested_then_403(monkeypatch):
    # /api/status previously checked authentication only (audit finding D) --
    # a principal holding no role at all must now be refused the same way
    # /api/documents already is.
    principal = PrincipalContext(subject="viewer", tenant_id="local-development", roles=frozenset())
    monkeypatch.setattr(app_module, "_authenticator", _FixedAuthenticator(principal))
    resp = TestClient(app).get("/api/status")
    assert resp.status_code == 403


def test_given_every_real_role_when_status_requested_then_200(monkeypatch):
    for roles in (frozenset({"adjuster"}), frozenset({"supervisor"}), frozenset({"siu"}), frozenset({"admin"})):
        principal = PrincipalContext(subject="u", tenant_id="local-development", roles=roles)
        monkeypatch.setattr(app_module, "_authenticator", _FixedAuthenticator(principal))
        resp = TestClient(app).get("/api/status")
        assert resp.status_code == 200, roles
