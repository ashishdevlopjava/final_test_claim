import json
import logging
import time
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlmodel import Session, select

from app.config import get_settings
from app.database import create_db_and_tables, get_session
from app.models import AuditEvent, Claim, ReviewTask
from app.schemas import (
    ClaimIntake,
    ClaimRead,
    Citation,
    DocumentIngest,
    DocumentRead,
    DocumentRequest,
    ReviewRead,
    ReviewSubmission,
    ToolEvidenceSearch,
    WorkflowRead,
)
from app.services.agent import get_workflow, run_claim_workflow
from app.services.retrieval import ingest_document
from app.tools import (
    claim_lookup,
    create_document_request,
    evidence_search,
    serialize_review,
    submit_review,
)

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger("claims_api")
request_count = 0


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(
    title="Insurance Claims Intelligence API",
    version="0.1.0",
    description="Local-first claim intake, evidence retrieval, and human-review workflow.",
    lifespan=lifespan,
)


@app.middleware("http")
async def operational_middleware(request: Request, call_next):
    global request_count
    request_count += 1
    started = time.perf_counter()
    if settings.api_key and request.url.path not in {"/health/live", "/health/ready"}:
        supplied_key = request.headers.get("x-api-key")
        if supplied_key != settings.api_key:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request.failed", extra={"path": request.url.path})
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        json.dumps(
            {
                "event": "request.completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
        )
    )
    response.headers["X-Process-Time-Ms"] = str(duration_ms)
    return response


@app.get("/", tags=["system"])
def index():
    return {"service": "insurance-claims-intelligence", "api_version": app.version}


@app.get("/health/live", tags=["system"])
def liveness():
    return {"status": "alive"}


@app.get("/health/ready", tags=["system"])
def readiness(session: Session = Depends(get_session)):
    session.exec(select(1)).first()
    return {"status": "ready", "provider": settings.llm_provider}


@app.get("/metrics", response_class=PlainTextResponse, tags=["system"])
def metrics(session: Session = Depends(get_session)):
    claim_count = len(session.exec(select(Claim.id)).all())
    open_reviews = len(
        session.exec(select(ReviewTask.id).where(ReviewTask.status == "open")).all()
    )
    return (
        "# TYPE claims_api_requests_total counter\n"
        f"claims_api_requests_total {request_count}\n"
        "# TYPE claims_total gauge\n"
        f"claims_total {claim_count}\n"
        "# TYPE claims_open_review_tasks gauge\n"
        f"claims_open_review_tasks {open_reviews}\n"
    )


@app.post("/claims", response_model=ClaimRead, status_code=201, tags=["claims"])
def create_claim(request: ClaimIntake, session: Session = Depends(get_session)):
    claim = Claim(**request.model_dump())
    session.add(claim)
    session.add(
        AuditEvent(
            action="claim.created",
            entity_type="claim",
            entity_id=claim.id,
            details_json=json.dumps({"policy_number_present": bool(claim.policy_number)}),
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


@app.get("/claims/{claim_id}", response_model=ClaimRead, tags=["claims"])
def get_claim(claim_id: str, session: Session = Depends(get_session)):
    return claim_lookup(session, claim_id)


@app.get("/tools/claim-lookup/{claim_id}", response_model=ClaimRead, tags=["agent tools"])
def claim_lookup_tool(claim_id: str, session: Session = Depends(get_session)):
    return claim_lookup(session, claim_id)


@app.post("/documents", response_model=DocumentRead, status_code=201, tags=["evidence"])
def add_document(request: DocumentIngest, session: Session = Depends(get_session)):
    result = ingest_document(session, request)
    session.add(
        AuditEvent(
            action="document.ingested",
            entity_type="document",
            entity_id=result.id,
            details_json=json.dumps({"title": result.title, "chunks": result.chunk_count}),
        )
    )
    session.commit()
    return result


@app.post("/tools/evidence-search", response_model=list[Citation], tags=["agent tools"])
def search_tool(request: ToolEvidenceSearch, session: Session = Depends(get_session)):
    return evidence_search(session, request)


@app.post(
    "/tools/claims/{claim_id}/document-request",
    response_model=ReviewRead,
    status_code=201,
    tags=["agent tools"],
)
def document_request_tool(
    claim_id: str,
    request: DocumentRequest,
    session: Session = Depends(get_session),
):
    return create_document_request(session, claim_id, request)


@app.post("/claims/{claim_id}/workflow", response_model=WorkflowRead, tags=["workflow"])
async def start_workflow(claim_id: str, session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return await run_claim_workflow(session, claim)


@app.get("/workflows/{workflow_id}", response_model=WorkflowRead, tags=["workflow"])
def workflow_status(workflow_id: str, session: Session = Depends(get_session)):
    workflow = get_workflow(session, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@app.get("/claims/{claim_id}/review-tasks", response_model=list[ReviewRead], tags=["review"])
def list_review_tasks(claim_id: str, session: Session = Depends(get_session)):
    if not session.get(Claim, claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    tasks = session.exec(
        select(ReviewTask)
        .where(ReviewTask.claim_id == claim_id)
        .order_by(ReviewTask.created_at.desc())
    ).all()
    return [serialize_review(task) for task in tasks]


@app.post("/review-tasks/{task_id}/submit", response_model=ReviewRead, tags=["review"])
def review_task(
    task_id: str,
    request: ReviewSubmission,
    session: Session = Depends(get_session),
):
    return submit_review(session, task_id, request)


@app.get("/audit", tags=["operations"])
def list_audit_events(
    limit: int = 100,
    session: Session = Depends(get_session),
):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return session.exec(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    ).all()
