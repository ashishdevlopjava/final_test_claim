import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import database
from app.config import Settings
from app.database import get_session
from app.main import app
from app.schemas import Citation
from app.services.llm import generate_assessment


@pytest.fixture
def client(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(database, "engine", test_engine)

    def override_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    test_engine.dispose()


def test_claim_to_grounded_review_flow(client):
    policy = client.post(
        "/documents",
        json={
            "title": "Home Policy HP-101",
            "category": "policy",
            "policy_number": "HP-101",
            "content": (
                "Sudden and accidental water discharge from a plumbing system is covered. "
                "The insured must prevent additional damage and provide photographs, "
                "repair invoices, and a description of the water leak."
            ),
        },
    )
    assert policy.status_code == 201
    assert policy.json()["chunk_count"] >= 1

    claim_response = client.post(
        "/claims",
        json={
            "policy_number": "HP-101",
            "claimant_name": "Morgan Lee",
            "incident_date": "2026-09-10",
            "description": "A sudden water leak damaged the kitchen floor and cabinets.",
            "requested_amount": 4200,
        },
    )
    assert claim_response.status_code == 201
    claim_id = claim_response.json()["id"]

    lookup = client.get(f"/tools/claim-lookup/{claim_id}")
    assert lookup.status_code == 200
    assert lookup.json()["policy_number"] == "HP-101"

    evidence = client.post(
        "/tools/evidence-search",
        json={"query": "sudden accidental water leak repair invoices", "policy_number": "HP-101"},
    )
    assert evidence.status_code == 200
    assert evidence.json()
    assert evidence.json()[0]["citation"].startswith("Home Policy HP-101#chunk-")

    workflow_response = client.post(f"/claims/{claim_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    assert workflow["status"] == "human_review"
    assert "claim_lookup" in workflow["state"]["completed_tools"]
    assert "evidence_search" in workflow["state"]["completed_tools"]
    assert workflow["result"]["citations"]
    workflow_id = workflow["id"]

    persisted = client.get(f"/workflows/{workflow_id}")
    assert persisted.status_code == 200
    assert persisted.json()["state"]["step"] == "awaiting_human_review"

    tasks = client.get(f"/claims/{claim_id}/review-tasks")
    assert tasks.status_code == 200
    task_id = tasks.json()[0]["id"]
    submitted = client.post(
        f"/review-tasks/{task_id}/submit",
        json={"decision": "approve", "note": "Reviewed policy wording and submitted invoices."},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "approve"
    assert client.get(f"/workflows/{workflow_id}").json()["status"] == "completed"
    assert any(event["action"] == "review.submitted" for event in client.get("/audit").json())


def test_claim_validation_and_unknown_claim(client):
    invalid_claim = client.post(
        "/claims",
        json={
            "policy_number": "HP-1",
            "claimant_name": "A",
            "incident_date": "2026-09-10",
            "description": "short",
            "requested_amount": -1,
        },
    )
    assert invalid_claim.status_code == 422
    invalid_scalar = client.post(
        "/claims",
        json={
            "policy_number": 101,
            "claimant_name": "Taylor Reed",
            "incident_date": "2026-09-10",
            "description": "Water damage to the kitchen floor.",
            "requested_amount": 100,
        },
    )
    assert invalid_scalar.status_code == 422
    assert client.post("/claims/not-a-claim/workflow").status_code == 404
    assert client.get("/tools/claim-lookup/not-a-claim").status_code == 404


def test_provider_configuration_failure_falls_back_locally():
    settings = Settings(llm_provider="openai", llm_api_key=None, llm_base_url=None)
    citation = Citation(
        citation="policy#chunk-1",
        document_title="policy",
        category="policy",
        excerpt="Sudden water damage is covered.",
        score=0.5,
    )
    result = asyncio.run(
        generate_assessment({"id": "claim-1"}, [citation], settings=settings)
    )
    assert result.provider_fallback is True
    assert result.citations[0].citation == "policy#chunk-1"


def test_provider_timeout_retries_then_falls_back(monkeypatch):
    class TimeoutCompletions:
        calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            raise TimeoutError("provider timed out")

    class TimeoutClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": TimeoutCompletions()})()

        async def close(self):
            return None

    fake_client = TimeoutClient()
    monkeypatch.setattr("app.services.llm.AsyncOpenAI", lambda **kwargs: fake_client)
    settings = Settings(
        llm_provider="openai",
        llm_api_key="test-key",
        llm_base_url="https://model.invalid/v1",
        llm_max_retries=2,
    )
    result = asyncio.run(generate_assessment({"id": "claim-2"}, [], settings=settings))
    assert fake_client.chat.completions.calls == 3
    assert result.provider_fallback is True
    assert result.citations == []


def test_review_task_cannot_be_submitted_twice(client):
    claim = client.post(
        "/claims",
        json={
            "policy_number": "HP-102",
            "claimant_name": "Jordan Kim",
            "incident_date": "2026-09-01",
            "description": "A pipe burst and damaged the utility room floor.",
            "requested_amount": 900,
        },
    ).json()
    task = client.post(
        f"/tools/claims/{claim['id']}/document-request",
        json={"items": ["Repair invoice"], "reason": "Invoice not received."},
    ).json()
    submission = {"decision": "request_information", "note": "Please upload the invoice."}
    assert client.post(f"/review-tasks/{task['id']}/submit", json=submission).status_code == 200
    assert client.post(f"/review-tasks/{task['id']}/submit", json=submission).status_code == 409