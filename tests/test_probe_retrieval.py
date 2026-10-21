from scripts.probe_retrieval import _expected, _facts, summarize


def test_facts_are_the_reference_numbers_without_thousands_separators():
    assert _facts("Capped at $5,000; the $3,500 receipt and 30 days.") == ["30", "3500", "5000"]


def test_expected_accepts_a_single_source_a_list_or_none():
    assert _expected({"source": "a.pdf"}) == ["a.pdf"]
    assert _expected({"source": ["a.pdf", "b.pdf"]}) == ["a.pdf", "b.pdf"]
    assert _expected({"source": None}) == []


def test_summary_counts_unranked_sources_as_misses():
    rows = [
        {"ranks": {"a.pdf": 1}, "served": True, "facts": 2, "facts_found": 2},
        {"ranks": {"b.pdf": None}, "served": False, "facts": 2, "facts_found": 0},
    ]
    s = summarize(rows, top_k=15)
    assert s["all_sources_in_top4"] == 1
    assert s["all_sources_served_at_top_k4"] == 1
    assert s["mrr"] == 0.5
    assert s["missing"] == 1
    assert s["fact_recall"] == 0.5
