import pytest
from pydantic import ValidationError

from backend.contracts import GroundedResponse, structured_projection


PIPELINE = {
    "embedding_model": "embed",
    "reranker_model": "rerank",
    "generation_model": "simulated",
    "retrieved_at": "2026-08-28T00:00:00Z",
}


def test_given_evidence_when_response_is_built_then_decision_defaults_to_not_a_decision():
    response = GroundedResponse.model_validate(
        {
            "request_id": "req-1",
            "answer": {"text": "Supported", "status": "grounded", "interpretation": {"claims": []}},
            "evidence": [
                {
                    "id": "ev-1",
                    "document_id": "doc-1",
                    "document_version": "sha256:x",
                    "filename": "a.txt",
                    "chunk_id": "chunk-1",
                    "excerpt": "text",
                    "retrieval": {"method": "hybrid", "rank": 1, "score": 0.9},
                }
            ],
            "pipeline": PIPELINE,
        }
    )
    assert response.answer.decision_boundary.decision_status == "not_a_decision"
    assert response.answer.decision_boundary.human_action_required is True


def test_given_evidence_reference_that_does_not_exist_when_response_is_validated_then_it_is_rejected():
    with pytest.raises(ValidationError, match="evidence references"):
        GroundedResponse.model_validate(
            {
                "request_id": "req-1",
                "answer": {
                    "text": "Unsupported",
                    "status": "grounded",
                    "interpretation": {"claims": [{"text": "x", "evidence_ids": ["missing"]}]},
                },
                "evidence": [],
                "pipeline": PIPELINE,
            }
        )


def test_given_grounded_answer_without_evidence_then_validation_is_rejected():
    with pytest.raises(ValidationError, match="require evidence"):
        GroundedResponse.model_validate(
            {
                "request_id": "req-1",
                "answer": {"text": "x", "status": "grounded"},
                "evidence": [],
                "pipeline": PIPELINE,
            }
        )


def test_given_unknown_evidence_fields_then_validation_is_rejected():
    with pytest.raises(ValidationError):
        GroundedResponse.model_validate(
            {
                "request_id": "req-1",
                "answer": {"text": "x", "status": "insufficient_evidence"},
                "evidence": [{"id": "e", "unexpected": True}],
                "pipeline": PIPELINE,
            }
        )


def test_given_legacy_sources_when_projected_then_each_source_gets_stable_evidence_id():
    response = structured_projection(
        request_id="req-1",
        legacy_result={
            "answer": "Supported",
            "engine": "simulated",
            "sources": [{"filename": "policy.txt", "content": "cap is 1000", "score": 0.9, "id": 7}],
        },
        embedding_model="embed",
        reranker_model="rerank",
    )
    assert response.evidence[0].id == "ev_1"
    assert response.evidence[0].chunk_id == "7"
    assert response.answer.decision_boundary.decision_status == "not_a_decision"
