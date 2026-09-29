import hashlib
import json
import math
import re

from sqlmodel import Session, select

from app.models import EvidenceChunk, EvidenceDocument
from app.schemas import Citation, DocumentIngest, DocumentRead, ToolEvidenceSearch

TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
VECTOR_SIZE = 2048


def embed_text(text: str) -> list[float]:
    vector = [0.0] * VECTOR_SIZE
    for token in TOKEN_PATTERN.findall(text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest[:4], "little") % VECTOR_SIZE
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[value] += sign
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else vector


def split_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    normalized = " ".join(text.split())
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


def ingest_document(session: Session, request: DocumentIngest) -> DocumentRead:
    document = EvidenceDocument(
        title=request.title.strip(),
        category=request.category,
        policy_number=request.policy_number,
        content=request.content.strip(),
    )
    session.add(document)
    session.flush()
    chunks = split_chunks(request.content, request.chunk_size, request.chunk_overlap)
    for index, chunk in enumerate(chunks, start=1):
        citation = f"{document.title}#chunk-{index}"
        session.add(
            EvidenceChunk(
                document_id=document.id,
                text=chunk,
                citation=citation,
                embedding=json.dumps(embed_text(chunk)),
            )
        )
    session.commit()
    session.refresh(document)
    return DocumentRead(
        id=document.id,
        title=document.title,
        category=document.category,
        policy_number=document.policy_number,
        chunk_count=len(chunks),
    )


def search_evidence(session: Session, request: ToolEvidenceSearch) -> list[Citation]:
    query_vector = embed_text(request.query)
    statement = select(EvidenceChunk, EvidenceDocument).join(
        EvidenceDocument, EvidenceChunk.document_id == EvidenceDocument.id
    )
    if request.policy_number:
        statement = statement.where(
            (EvidenceDocument.policy_number == request.policy_number)
            | (EvidenceDocument.policy_number.is_(None))
        )

    ranked: list[tuple[float, EvidenceChunk, EvidenceDocument]] = []
    for chunk, document in session.exec(statement).all():
        stored = json.loads(chunk.embedding)
        score = sum(left * right for left, right in zip(query_vector, stored, strict=True))
        if score > 0:
            ranked.append((score, chunk, document))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        Citation(
            citation=chunk.citation,
            document_title=document.title,
            category=document.category,
            excerpt=chunk.text,
            score=round(score, 4),
        )
        for score, chunk, document in ranked[: request.top_k]
    ]
