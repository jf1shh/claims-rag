"""The keyword leg's query builder (backend/rag_engine.py fts_terms / fts_match_query)."""

from backend.rag_engine import FTS_MAX_TERMS, fts_match_query, fts_terms


def test_drops_stopwords_and_punctuation_and_dedupes():
    q = "What is the OEM eligibility for the claim's 2024 vehicle? OEM parts, please."
    assert fts_terms(q) == ["oem", "eligibility", "claim", "2024", "vehicle", "parts", "please"]


def test_splits_money_and_hyphenated_tokens():
    assert fts_terms("over $1,500 for zero-deductible glass") == ["over", "500", "zero", "deductible", "glass"]


def test_operator_words_are_never_bare():
    match = fts_match_query("deductible or not covered NEAR glass AND windshield")
    assert match == '"deductible" OR "covered" OR "near" OR "glass" OR "windshield"'


def test_postgres_joiner():
    assert fts_match_query("towing storage", joiner=" or ") == '"towing" or "storage"'


def test_only_stopwords_or_symbols_yield_empty_query():
    assert fts_match_query("what is it?") == ""
    assert fts_match_query('"*" ^ () :') == ""


def test_quote_characters_cannot_escape_the_term():
    # Only [a-z0-9] survive, so a query cannot inject FTS5 syntax.
    match = fts_match_query('glass" OR content:* "x')
    assert match == '"glass" OR "content"'


def test_term_cap_bounds_query_size():
    q = " ".join(f"term{i}" for i in range(FTS_MAX_TERMS * 3))
    assert len(fts_terms(q)) == FTS_MAX_TERMS
