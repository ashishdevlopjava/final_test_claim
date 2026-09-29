from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClaimIntake(BaseModel):
    policy_number: str = Field(min_length=3, max_length=64)
    claimant_name: str = Field(min_length=2, max_length=160)
    incident_date: date
    description: str = Field(min_length=10, max_length=5000)
    requested_amount: float = Field(gt=0, le=10_000_000)

    @field_validator("policy_number", "claimant_name", "description", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("incident_date")
    @classmethod
    def incident_date_is_not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("incident_date cannot be in the future")
        return value


class ClaimRead(ClaimIntake):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    created_at: datetime


class DocumentIngest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    category: Literal["policy", "evidence", "claim_form", "other"]
    content: str = Field(min_length=20, max_length=200_000)
    policy_number: str | None = Field(default=None, max_length=64)
    chunk_size: int = Field(default=700, ge=200, le=2000)
    chunk_overlap: int = Field(default=100, ge=0, le=500)

    @field_validator("chunk_overlap")
    @classmethod
    def overlap_less_than_chunk_size(cls, value: int, info):
        chunk_size = info.data.get("chunk_size", 700)
        if value >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return value


class DocumentRead(BaseModel):
    id: str
    title: str
    category: str
    policy_number: str | None
    chunk_count: int


class Citation(BaseModel):
    citation: str
    document_title: str
    category: str
    excerpt: str
    score: float


class Assessment(BaseModel):
    summary: str
    coverage_observations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    missing_documents: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    provider: str = "mock"
    provider_fallback: bool = False


class WorkflowRead(BaseModel):
    id: str
    claim_id: str
    status: str
    state: dict
    result: Assessment
    review_task_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ToolEvidenceSearch(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    policy_number: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class DocumentRequest(BaseModel):
    items: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("items")
    @classmethod
    def trim_requested_items(cls, value: list[str]) -> list[str]:
        items = [item.strip() for item in value]
        if any(not item for item in items):
            raise ValueError("document request items cannot be blank")
        return list(dict.fromkeys(items))

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ReviewSubmission(BaseModel):
    decision: Literal["approve", "deny", "request_information"]
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ReviewRead(BaseModel):
    id: str
    claim_id: str
    workflow_id: str
    reason: str
    requested_items: list[str]
    status: str
    reviewer_note: str | None
    created_at: datetime
    reviewed_at: datetime | None
