import json
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.config import get_settings
from app.models import AuditEvent, Claim, ReviewTask, WorkflowRun
from app.schemas import Assessment, ToolEvidenceSearch, WorkflowRead
from app.services.llm import generate_assessment
from app.services.retrieval import search_evidence
from app.services.rules import evaluate_review_rules


async def run_claim_workflow(session: Session, claim: Claim) -> WorkflowRead:
    workflow = WorkflowRun(
        claim_id=claim.id,
        status="running",
        state_json=json.dumps({"step": "claim_lookup", "completed_tools": []}),
    )
    session.add(workflow)
    session.flush()

    tool_results = [{"tool": "claim_lookup", "claim_id": claim.id, "status": claim.status}]
    state = {"step": "evidence_search", "completed_tools": ["claim_lookup"]}
    session.add(
        AuditEvent(
            action="tool.claim_lookup",
            entity_type="claim",
            entity_id=claim.id,
            details_json=json.dumps({"workflow_id": workflow.id}),
        )
    )

    search_request = ToolEvidenceSearch(
        query=f"{claim.description} policy coverage exclusions required documentation",
        policy_number=claim.policy_number,
        top_k=get_settings().retrieval_top_k,
    )
    citations = search_evidence(session, search_request)
    tool_results.append({"tool": "evidence_search", "citation_count": len(citations)})
    state = {"step": "rules", "completed_tools": ["claim_lookup", "evidence_search"]}
    session.add(
        AuditEvent(
            action="tool.evidence_search",
            entity_type="claim",
            entity_id=claim.id,
            details_json=json.dumps({"workflow_id": workflow.id, "citation_count": len(citations)}),
        )
    )

    assessment = await generate_assessment(claim.model_dump(mode="json"), citations)
    has_policy_evidence = any(item.category == "policy" for item in citations)
    status, reasons = evaluate_review_rules(claim, has_policy_evidence)
    assessment.missing_documents = assessment.missing_documents or (
        [] if has_policy_evidence else ["Applicable policy schedule or policy wording"]
    )
    review = ReviewTask(
        claim_id=claim.id,
        workflow_id=workflow.id,
        reason=" ".join(reasons),
        requested_items_json=json.dumps(assessment.missing_documents),
    )
    session.add(review)
    session.flush()
    state = {
        "step": "awaiting_human_review",
        "completed_tools": ["claim_lookup", "evidence_search", "document_request_review_task"],
        "tool_results": tool_results + [{"tool": "document_request_review_task", "task_id": review.id}],
    }
    workflow.status = status
    workflow.state_json = json.dumps(state)
    workflow.result_json = assessment.model_dump_json()
    workflow.updated_at = datetime.now(UTC)
    claim.status = status
    session.add(
        AuditEvent(
            action="workflow.completed_for_review",
            entity_type="workflow",
            entity_id=workflow.id,
            details_json=json.dumps({"claim_id": claim.id, "review_task_id": review.id}),
        )
    )
    session.commit()
    session.refresh(workflow)
    return WorkflowRead(
        id=workflow.id,
        claim_id=claim.id,
        status=workflow.status,
        state=state,
        result=Assessment.model_validate_json(workflow.result_json),
        review_task_id=review.id,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def get_workflow(session: Session, workflow_id: str) -> WorkflowRead | None:
    workflow = session.get(WorkflowRun, workflow_id)
    if not workflow:
        return None
    state = json.loads(workflow.state_json)
    review = session.exec(
        select(ReviewTask).where(ReviewTask.workflow_id == workflow.id)
    ).first()
    return WorkflowRead(
        id=workflow.id,
        claim_id=workflow.claim_id,
        status=workflow.status,
        state=state,
        result=Assessment.model_validate_json(workflow.result_json),
        review_task_id=review.id if review else None,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )
