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


def test_summary_counts_rows_where_a_distractor_outranks_the_source():
    rows = [
        {"ranks": {"a.pdf": 2}, "served": True, "facts": 1, "facts_found": 1, "distractor_outranks": True},
        {"ranks": {"b.pdf": 1}, "served": True, "facts": 1, "facts_found": 1, "distractor_outranks": False},
    ]
    assert summarize(rows, top_k=15)["distractor_outranks"] == 1


def test_hard_rows_are_well_formed_and_their_documents_exist():
    import os

    from eval.golden_hard_queries import HARD_GOLDEN_QUERIES
    from eval.golden_queries import GOLDEN_QUERIES
    from scripts.probe_retrieval import _expected

    hard_dir = os.path.join(os.path.dirname(__file__), "..", "eval", "hard_corpus")
    extra = set(os.listdir(hard_dir))
    # The original corpus (rag_store.db) is not in git, so the originals are the
    # documents the original golden rows already name.
    original = {name for r in GOLDEN_QUERIES for name in _expected(r)}
    ids = [r["id"] for r in HARD_GOLDEN_QUERIES]
    assert len(ids) == len(set(ids))
    for row in HARD_GOLDEN_QUERIES:
        assert row["query"] and row["reference"] and _expected(row)
        assert row["kind"] in {"near-miss", "multi-source", "paraphrase"}
        for name in _expected(row):
            assert name in extra or name in original, f"{row['id']}: unknown source {name}"
        for name in row.get("avoid", []):
            assert name not in _expected(row)
            if name.endswith(".txt"):
                assert name in extra
            else:
                assert name in original, f"{row['id']}: unknown avoid {name}"


def test_facts_match_whole_numbers_only():
    from scripts.probe_retrieval import _fact_in

    assert _fact_in("8", "a rate of 8% applies")
    assert not _fact_in("8", "a rate of 18% or 2008")
    assert not _fact_in("15", "up to 150 miles")
    assert not _fact_in("5", "within 0.5 days")
    assert _fact_in("0.60", "pays 0.60 per mile")
    assert _fact_in("5000", "capped at 5000.")
