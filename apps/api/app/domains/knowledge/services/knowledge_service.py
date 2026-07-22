import re
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.knowledge.services import embedding_service, qdrant_service
from app.core.ids import generate_id
from app.core.time import now_iso


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MARKDOWN_FENCE_RE = re.compile(r"^\s*(```|~~~)")


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


def split_markdown_sections(
    text: str,
    max_heading_level: int = 6,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    buffer: list[str] = []
    heading_stack: dict[int, str] = {}
    current_section: str | None = None
    active_fence: str | None = None

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            sections.append({"text": content, "page": None, "section": current_section})

    for line in text.splitlines():
        fence = MARKDOWN_FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            active_fence = None if active_fence == marker else marker

        heading = MARKDOWN_HEADING_RE.match(line) if active_fence is None else None
        if heading and len(heading.group(1)) <= max_heading_level:
            flush()
            buffer = [line]
            level = len(heading.group(1))
            title = re.sub(r"\s+#+\s*$", "", heading.group(2)).strip()
            heading_stack = {
                saved_level: saved_title
                for saved_level, saved_title in heading_stack.items()
                if saved_level < level
            }
            heading_stack[level] = title
            current_section = " / ".join(
                heading_stack[saved_level] for saved_level in sorted(heading_stack)
            )
        else:
            buffer.append(line)

    flush()
    return sections


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
    if path.suffix.lower() == ".md" and settings.chunk_strategy == "adaptive":
        units = split_markdown_sections(
            parsed_pages[0]["text"],
            max_heading_level=settings.markdown_heading_max_level,
        )
    else:
        units = [
            {"text": page["text"], "page": page["page"], "section": None}
            for page in parsed_pages
        ]

    records: list[dict[str, Any]] = []
    for unit in units:
        for text in chunk_text(
            unit["text"], settings.chunk_size, settings.chunk_overlap
        ):
            records.append(
                {
                    "text": text,
                    "page": unit["page"],
                    "section": unit["section"],
                }
            )

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
            "section": record["section"],
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
