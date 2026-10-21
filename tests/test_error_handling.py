"""Audit finding C: an unhandled exception must produce the generic 500 JSON
body and never let its message (which can carry filenames/claim ids) reach
the server log or the ASGI server's own traceback print."""

import logging

from fastapi.testclient import TestClient

from backend.app import app
from backend.app import runtime as app_module


def test_given_unhandled_exception_when_raised_then_generic_500_and_no_message_in_log(monkeypatch, caplog):
    def _boom():
        raise ValueError("secret-claim-#2026-1 file.pdf")

    monkeypatch.setattr(app_module.vector_store, "get_all_documents", _boom)

    with caplog.at_level(logging.ERROR):
        response = TestClient(app).get("/api/documents")

    assert response.status_code == 500
    assert response.json() == {"detail": "An internal error occurred."}
    assert "secret-claim" not in caplog.text
