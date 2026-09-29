from datetime import UTC, date, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


class Claim(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    policy_number: str = Field(index=True)
    claimant_name: str
    incident_date: date
    description: str
    requested_amount: float
    status: str = Field(default="submitted", index=True)
    created_at: datetime = Field(default_factory=now_utc)


class EvidenceDocument(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    title: str
    category: str = Field(index=True)
    policy_number: str | None = Field(default=None, index=True)
    content: str
    created_at: datetime = Field(default_factory=now_utc)


class EvidenceChunk(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    document_id: str = Field(foreign_key="evidencedocument.id", index=True)
    text: str
    citation: str
    embedding: str


class WorkflowRun(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    claim_id: str = Field(foreign_key="claim.id", index=True)
    status: str = Field(default="running", index=True)
    state_json: str = Field(default="{}")
    result_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ReviewTask(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    claim_id: str = Field(foreign_key="claim.id", index=True)
    workflow_id: str = Field(foreign_key="workflowrun.id", index=True)
    reason: str
    requested_items_json: str = Field(default="[]")
    status: str = Field(default="open", index=True)
    reviewer_note: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    reviewed_at: datetime | None = None


class AuditEvent(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    action: str = Field(index=True)
    actor: str = "system"
    entity_type: str
    entity_id: str
    details_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=now_utc, index=True)
