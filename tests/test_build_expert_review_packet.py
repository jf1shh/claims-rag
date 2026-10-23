from eval.build_expert_review_packet import format_packet, sample_queries

QUERIES = [
    {"id": "g1", "claim_id": None, "query": "q1", "reference": "r1"},
    {"id": "g2", "claim_id": None, "query": "q2", "reference": "r2"},
    {"id": "g3", "claim_id": None, "query": "q3", "reference": "r3"},
    {"id": "c1a", "claim_id": "#1", "query": "q4", "reference": "r4"},
    {"id": "c1b", "claim_id": "#1", "query": "q5", "reference": "r5"},
    {"id": "c2a", "claim_id": "#2", "query": "q6", "reference": "r6"},
]


def test_sample_queries_covers_every_claim_exactly_once():
    sample = sample_queries(QUERIES, n_global=1, seed=1)
    claim_ids = [q["claim_id"] for q in sample if q["claim_id"] is not None]
    assert sorted(claim_ids) == ["#1", "#2"]
    assert claim_ids.count("#1") == 1  # first query per claim, not both


def test_sample_queries_is_deterministic():
    a = sample_queries(QUERIES, n_global=2, seed=7)
    b = sample_queries(QUERIES, n_global=2, seed=7)
    assert [q["id"] for q in a] == [q["id"] for q in b]


def test_sample_queries_caps_global_slice_at_available_count():
    sample = sample_queries(QUERIES, n_global=99, seed=1)
    global_ids = [q["id"] for q in sample if q["claim_id"] is None]
    assert sorted(global_ids) == ["g1", "g2", "g3"]


def test_format_packet_includes_answer_sources_and_blank_scores():
    rows = [{
        **QUERIES[0],
        "chat_result": {
            "answer": "The cap is $110/hr.",
            "sources": [{"filename": "SOP_Auto_Repair_Labor_Rates.pdf"}],
        },
    }]
    md = format_packet(rows, scored_by_id={"g1": {"faithfulness": 0.9, "correctness": 0.8}})
    assert "The cap is $110/hr." in md
    assert "SOP_Auto_Repair_Labor_Rates.pdf" in md
    assert "faithfulness=0.9" in md
    assert "_Score:_" in md


def test_format_packet_handles_a_failed_chat_call():
    rows = [{**QUERIES[0], "chat_result": None}]
    md = format_packet(rows)
    assert "no answer" in md
