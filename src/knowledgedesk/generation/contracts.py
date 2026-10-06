from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AnswerStatus = Literal[
    "answered",
    "partial",
    "insufficient_evidence",
    "needs_clarification",
    "conflicting_sources",
]


class AnswerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=1)


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AnswerStatus
    answer_blocks: list[AnswerBlock] = Field(default_factory=list, max_length=12)
    missing_information: list[str] = Field(default_factory=list, max_length=8)
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_status_shape(self) -> GroundedAnswer:
        if self.status == "needs_clarification" and not self.clarification_question:
            raise ValueError("a clarification question is required for this status")
        if self.status in {"insufficient_evidence", "needs_clarification"} and self.answer_blocks:
            raise ValueError("this status cannot contain factual answer blocks")
        if self.status in {"answered", "partial", "conflicting_sources"} and not self.answer_blocks:
            raise ValueError("this status requires evidence-backed answer blocks")
        return self

    def validate_evidence_ids(
        self, allowed_ids: set[str], source_groups: dict[str, str] | None = None
    ) -> None:
        cited = {evidence_id for block in self.answer_blocks for evidence_id in block.evidence_ids}
        if not cited.issubset(allowed_ids):
            raise ValueError("answer cites evidence that was not supplied to the model")
        if self.status == "conflicting_sources" and source_groups is not None:
            cited_groups = {source_groups[evidence_id] for evidence_id in cited}
            if len(cited_groups) < 2:
                raise ValueError("conflicting sources require evidence from at least two documents")
