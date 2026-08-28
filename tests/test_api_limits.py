import pytest
from config import Settings
from backend.app import SearchRequest


def test_given_query_over_maximum_length_when_validated_then_request_is_rejected():
    settings = Settings.from_env({"MAX_QUERY_CHARS": "10"})
    with pytest.raises(ValueError):
        SearchRequest(query="x" * 11, mode="naive", top_k=4).validate_limits(settings)


def test_given_top_k_over_configured_limit_when_validated_then_request_is_rejected():
    settings = Settings.from_env({"MAX_TOP_K": "4"})
    with pytest.raises(ValueError):
        SearchRequest(query="valid", mode="naive", top_k=5).validate_limits(settings)
