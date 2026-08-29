from fastapi.testclient import TestClient
import backend.app as app_module
from backend.app import app
from backend.llm_client import OpenAICompatibleClient


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
