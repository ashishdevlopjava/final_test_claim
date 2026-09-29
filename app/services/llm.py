import asyncio
import json
import logging

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.schemas import Assessment, Citation

logger = logging.getLogger(__name__)


def grounded_fallback(claim: dict, citations: list[Citation], provider: str = "mock") -> Assessment:
    if citations:
        summary = (
            f"Retrieved {len(citations)} relevant source passage(s) for claim {claim['id']}. "
            "This is an evidence summary, not a coverage determination."
        )
        observations = [
            f"{item.category.title()} source {item.citation} may be relevant; verify the cited text."
            for item in citations[:3]
        ]
        confidence = min(0.82, 0.45 + max(item.score for item in citations) * 0.35)
    else:
        summary = "No matching policy or evidence passages were found; human review is required."
        observations = ["The available corpus does not support a grounded coverage observation."]
        confidence = 0.1
    return Assessment(
        summary=summary,
        coverage_observations=observations,
        confidence=round(confidence, 2),
        missing_documents=[],
        citations=citations,
        provider=provider,
    )


async def generate_assessment(
    claim: dict, citations: list[Citation], settings: Settings | None = None
) -> Assessment:
    config = settings or get_settings()
    if config.llm_provider == "mock":
        return grounded_fallback(claim, citations)

    endpoint = config.resolved_llm_base_url
    api_key = config.resolved_llm_api_key
    if not endpoint or not api_key:
        logger.warning("LLM provider configuration incomplete; using local grounded fallback")
        result = grounded_fallback(claim, citations)
        result.provider_fallback = True
        return result

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        timeout=config.llm_timeout_seconds,
        max_retries=0,
    )
    context = [item.model_dump() for item in citations]
    prompt = {
        "claim": claim,
        "retrieved_evidence": context,
        "requirements": [
            "Return only JSON matching the requested schema.",
            "Cite only citation identifiers present in retrieved_evidence.",
            "Do not infer coverage when evidence is absent; state uncertainty.",
            "Never issue a final approve or deny decision.",
        ],
    }
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "claim_assessment",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "coverage_observations": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "missing_documents": {"type": "array", "items": {"type": "string"}},
                    "citation_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "summary", "coverage_observations", "confidence",
                    "missing_documents", "citation_ids",
                ],
                "additionalProperties": False,
            },
        },
    }
    try:
        response = None
        for attempt in range(config.llm_max_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=config.resolved_llm_model,
                    messages=[
                        {"role": "system", "content": "You produce cautious, evidence-grounded claim summaries."},
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    response_format=schema,
                )
                break
            except Exception:
                if attempt >= config.llm_max_retries:
                    raise
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
        if response is None or not response.choices[0].message.content:
            raise ValueError("LLM returned an empty response")
        payload = json.loads(response.choices[0].message.content)
        result = Assessment.model_validate(
            {
                **payload,
                "citations": [
                    citation.model_dump()
                    for citation in citations
                    if citation.citation in payload.get("citation_ids", [])
                ],
                "provider": config.llm_provider,
            }
        )
        return result
    except (Exception, ValidationError) as exc:
        logger.exception("LLM assessment failed; using deterministic evidence fallback", extra={"error_type": type(exc).__name__})
        result = grounded_fallback(claim, citations, provider=config.llm_provider)
        result.provider_fallback = True
        return result
    finally:
        await client.close()
