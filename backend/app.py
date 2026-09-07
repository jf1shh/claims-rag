"""Documented ASGI entry point; uses the same factory as integration tests."""
from app_factory import create_app
from backend.api import ChatRequest, DeleteRequest, SearchRequest  # noqa: F401

app = create_app()
runtime = app.state.runtime
