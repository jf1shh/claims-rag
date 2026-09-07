from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Calculation(BaseModel):
    operands: list[str] = Field(default_factory=list)
    operation: str
    result: str


class InterpretationClaim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    calculation: Calculation | None = None


class Interpretation(BaseModel):
    claims: list[InterpretationClaim] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high", "not_assessed"] = "not_assessed"


class DecisionBoundary(BaseModel):
    decision_status: Literal["not_a_decision", "recommended_for_review", "human_recorded"] = "not_a_decision"
    human_action_required: bool = True


class Answer(BaseModel):
    text: str
    status: Literal["grounded", "insufficient_evidence", "conflicting_evidence", "error"]
    interpretation: Interpretation = Field(default_factory=Interpretation)
    decision_boundary: DecisionBoundary = Field(default_factory=DecisionBoundary)


class RetrievalMetadata(BaseModel):
    method: str
    rank: int = Field(ge=1)
    score: float


class EvidenceLocator(BaseModel):
    page: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    document_version: str
    filename: str
    chunk_id: str
    excerpt: str
    locator: EvidenceLocator | None = None
    retrieval: RetrievalMetadata


class PipelineMetadata(BaseModel):
    embedding_model: str
    reranker_model: str
    generation_model: str
    retrieved_at: datetime


class GroundedResponse(BaseModel):
    request_id: str
    answer: Answer
    evidence: list[Evidence] = Field(default_factory=list)
    pipeline: PipelineMetadata

    @model_validator(mode="after")
    def evidence_references_must_resolve(self) -> "GroundedResponse":
        evidence_ids = {item.id for item in self.evidence}
        for claim in self.answer.interpretation.claims:
            missing = set(claim.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"evidence references do not resolve: {sorted(missing)}")
        if self.answer.status == "grounded" and not self.evidence:
            raise ValueError("grounded answers require evidence")
        return self


def evidence_from_matches(matches: list[dict[str, Any]], *, method: str = "hybrid_rerank") -> list[Evidence]:
    """Convert legacy retrieval matches into stable response evidence records."""
    return [
        Evidence(
            id=f"ev_{index}",
            document_id=str(match.get("document_id", match.get("filename", f"document-{index}"))),
            document_version=str(match.get("document_version", "legacy-unversioned")),
            filename=str(match["filename"]),
            chunk_id=str(match.get("id", f"chunk-{index}")),
            excerpt=str(match.get("content", "")),
            retrieval=RetrievalMetadata(
                method=method,
                rank=index,
                score=float(match.get("score", 0.0)),
            ),
        )
        for index, match in enumerate(matches, start=1)
    ]


def structured_projection(
    *,
    request_id: str,
    legacy_result: dict[str, Any],
    embedding_model: str,
    reranker_model: str,
) -> GroundedResponse:
    sources = legacy_result.get("sources") or []
    evidence = evidence_from_matches(sources)
    result_status = legacy_result.get("status")
    status = "error" if result_status == "error" else "grounded" if evidence else "insufficient_evidence"
    if "simulated" in str(legacy_result.get("engine", "")):
        status = "insufficient_evidence"
    answer_text = str(legacy_result.get("answer", ""))
    return build_grounded_response(
        request_id=request_id,
        text=answer_text,
        status=status,
        evidence=evidence,
        pipeline=pipeline_metadata(
            embedding_model=embedding_model,
            reranker_model=reranker_model,
            generation_model=str(legacy_result.get("engine", "unknown")),
        ),
    )


def pipeline_metadata(*, embedding_model: str, reranker_model: str, generation_model: str) -> PipelineMetadata:
    return PipelineMetadata(
        embedding_model=embedding_model,
        reranker_model=reranker_model,
        generation_model=generation_model,
        retrieved_at=datetime.now(timezone.utc),
    )


def build_grounded_response(
    *,
    request_id: str,
    text: str,
    status: Literal["grounded", "insufficient_evidence", "conflicting_evidence", "error"],
    evidence: list[Evidence],
    pipeline: PipelineMetadata,
    claims: list[InterpretationClaim] | None = None,
    assumptions: list[str] | None = None,
    uncertainty: Literal["low", "medium", "high", "not_assessed"] = "not_assessed",
    decision_boundary: DecisionBoundary | None = None,
) -> GroundedResponse:
    return GroundedResponse(
        request_id=request_id,
        answer=Answer(
            text=text,
            status=status,
            interpretation=Interpretation(
                claims=claims or [],
                assumptions=assumptions or [],
                uncertainty=uncertainty,
            ),
            decision_boundary=decision_boundary or DecisionBoundary(),
        ),
        evidence=evidence,
        pipeline=pipeline,
    )
