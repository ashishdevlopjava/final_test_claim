import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import AuditEvent, Claim, ReviewTask, WorkflowRun
from app.schemas import DocumentRequest, ReviewRead, ReviewSubmission, ToolEvidenceSearch
from app.services.retrieval import search_evidence


def claim_lookup(session: Session, claim_id: str) -> Claim:
    claim = session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    session.add(
        AuditEvent(action="tool.claim_lookup", entity_type="claim", entity_id=claim.id)
    )
    session.commit()
    return claim


def evidence_search(session: Session, request: ToolEvidenceSearch):
    citations = search_evidence(session, request)
    session.add(
        AuditEvent(
            action="tool.evidence_search",
            entity_type="query",
            entity_id="manual",
            details_json=json.dumps({"query_length": len(request.query), "results": len(citations)}),
        )
    )
    session.commit()
    return citations


def create_document_request(
    session: Session, claim_id: str, request: DocumentRequest
) -> ReviewRead:
    claim = session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    workflow = session.exec(
        select(WorkflowRun).where(WorkflowRun.claim_id == claim_id).order_by(WorkflowRun.created_at.desc())
    ).first()
    if not workflow:
        workflow = WorkflowRun(claim_id=claim_id, status="human_review")
        session.add(workflow)
        session.flush()
    task = ReviewTask(
        claim_id=claim_id,
        workflow_id=workflow.id,
        reason=request.reason,
        requested_items_json=json.dumps(request.items),
    )
    session.add(task)
    session.add(
        AuditEvent(
            action="tool.document_request",
            entity_type="review_task",
            entity_id=task.id,
            details_json=json.dumps({"claim_id": claim_id, "items": request.items}),
        )
    )
    session.commit()
    session.refresh(task)
    return serialize_review(task)


def serialize_review(task: ReviewTask) -> ReviewRead:
    return ReviewRead(
        id=task.id,
        claim_id=task.claim_id,
        workflow_id=task.workflow_id,
        reason=task.reason,
        requested_items=json.loads(task.requested_items_json),
        status=task.status,
        reviewer_note=task.reviewer_note,
        created_at=task.created_at,
        reviewed_at=task.reviewed_at,
    )


def submit_review(session: Session, task_id: str, submission: ReviewSubmission) -> ReviewRead:
    task = session.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found")
    if task.status != "open":
        raise HTTPException(status_code=409, detail="Review task is already closed")
    task.status = submission.decision
    task.reviewer_note = submission.note
    task.reviewed_at = datetime.now(UTC)
    workflow = session.get(WorkflowRun, task.workflow_id)
    if workflow:
        workflow.status = "completed"
        workflow.updated_at = task.reviewed_at
    claim = session.get(Claim, task.claim_id)
    if claim:
        claim.status = "completed"
    session.add(
        AuditEvent(
            action="review.submitted",
            actor="human_reviewer",
            entity_type="review_task",
            entity_id=task.id,
            details_json=json.dumps({"decision": submission.decision, "note": submission.note}),
        )
    )
    session.commit()
    session.refresh(task)
    return serialize_review(task)
