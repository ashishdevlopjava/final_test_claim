# Insurance Claims Intelligence API

A local-first FastAPI proof of concept for claim intake, evidence retrieval, grounded summaries, and human review. It runs without cloud credentials. LLM output is advisory only; an authorized adjuster makes the claim decision.

## Run Locally

From this directory in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API. SQLite data is stored in `claims.db`. Run tests with `python -m pytest`; run lint with `ruff check .`.

## End-to-End Flow

1. `POST /documents` stores policy/evidence text and splits it into overlapping chunks with deterministic, local feature-hash vectors.
2. `POST /claims` validates and persists claim intake.
3. `POST /claims/{claim_id}/workflow` runs claim lookup, policy/evidence retrieval, a rules-based routing step, and a structured assessment. The workflow state and tool trace are persisted.
4. The response includes source titles, chunk citations, excerpts, and retrieval scores. No cited evidence means the response says so and routes to review.
5. `GET /claims/{claim_id}/review-tasks` lists the human task. `POST /review-tasks/{task_id}/submit` records the reviewer decision and closes the workflow; a second submission returns `409`.

The three callable tool APIs are `GET /tools/claim-lookup/{claim_id}`, `POST /tools/evidence-search`, and `POST /tools/claims/{claim_id}/document-request`. The workflow records its tool execution state in the returned `state` object and the `audit` table.

## LLM Providers

The default `LLM_PROVIDER=mock` produces a deterministic, evidence-aware response with no external calls. To use Azure OpenAI, set `LLM_PROVIDER=azure_openai`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and `AZURE_OPENAI_DEPLOYMENT` in the ignored `.env` file. For a local OpenAI-compatible server, set `LLM_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`.

Provider calls use configured timeouts and bounded retries. Missing configuration, timeouts, provider errors, and malformed structured output fall back to the local grounded summary and mark `provider_fallback=true`. Never put secrets in source control or claim payloads.

## Retrieval and PoC Boundaries

The baseline uses normalized token feature hashing and cosine similarity. This keeps setup small and offline, but is lexical retrieval, not a learned semantic embedding model; production evaluation should compare a vetted local or managed embedding model and a vector index against representative, permission-filtered data. Chunk citations identify the source document and chunk ordinal.

All workflows route to human review. The configured amount threshold and evidence availability annotate routing reasons; they do not approve or deny claims. The included policy is fictional sample text only. The SQLite schema and API are demonstration-grade, not a production claims system: add identity/RBAC, encryption and retention controls, malware-scanned document upload, rate limits, robust migration management, and operational monitoring before handling real customer data.

If `API_KEY` is configured, protected requests require an `x-api-key` header; liveness and readiness endpoints remain available. `/health/live`, `/health/ready`, `/metrics`, and `/audit` provide basic operational visibility. Audit metadata intentionally avoids storing claim descriptions and retrieved document text.
