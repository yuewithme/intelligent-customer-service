from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.services import embedding_service, qdrant_service
from app.utils.ids import generate_id
from app.utils.time import now_iso


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


def parse_document(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppError(ErrorCode.UNSUPPORTED_FILE_TYPE)
    try:
        if suffix in {".txt", ".md"}:
            return [{"text": path.read_text(encoding="utf-8"), "page": None}]

        from pypdf import PdfReader

        return [
            {"text": page.extract_text() or "", "page": index}
            for index, page in enumerate(PdfReader(str(path)).pages, start=1)
        ]
    except AppError:
        raise
    except Exception as exc:
        raise AppError(ErrorCode.DOCUMENT_PARSE_FAILED) from exc


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks = []
    start = 0
    while start < len(cleaned):
        chunks.append(cleaned[start : start + chunk_size])
        if start + chunk_size >= len(cleaned):
            break
        start += chunk_size - overlap
    return chunks


async def index_document(
    *,
    path: Path,
    original_name: str,
    kb_id: str,
    tenant_id: str,
    permission: str,
) -> dict[str, Any]:
    settings = get_settings()
    doc_id = generate_id("document")
    parsed_pages = parse_document(path)
    records: list[dict[str, Any]] = []
    for page in parsed_pages:
        for text in chunk_text(
            page["text"], settings.chunk_size, settings.chunk_overlap
        ):
            records.append({"text": text, "page": page["page"]})

    vectors = await embedding_service.embed_texts(
        [record["text"] for record in records]
    )
    points = []
    for record, vector in zip(records, vectors):
        chunk_id = generate_id("chunk")
        payload = {
            "text": record["text"],
            "kb_id": kb_id,
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "file_name": original_name,
            "file_type": path.suffix.lower().lstrip("."),
            "page": record["page"],
            "section": None,
            "tenant_id": tenant_id,
            "permission": permission,
            "created_at": now_iso(),
        }
        points.append({"id": chunk_id, "vector": vector, "payload": payload})
    if points:
        await qdrant_service.upsert_chunks(points)
    return {
        "doc_id": doc_id,
        "file_name": original_name,
        "chunk_count": len(points),
        "status": "indexed",
    }

