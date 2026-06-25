from pathlib import Path

import pytest

from app.schemas.common import AppError, ErrorCode
from app.services import knowledge_service


def test_chunk_text_uses_overlap():
    chunks = knowledge_service.chunk_text("abcdefghij", chunk_size=6, overlap=2)
    assert chunks == ["abcdef", "efghij"]


def test_parse_document_rejects_unsupported_file(tmp_path):
    path = tmp_path / "manual.docx"
    path.write_bytes(b"content")

    with pytest.raises(AppError) as exc:
        knowledge_service.parse_document(path)

    assert exc.value.code == ErrorCode.UNSUPPORTED_FILE_TYPE


@pytest.mark.asyncio
async def test_index_document_builds_fixed_payload(monkeypatch, tmp_path):
    path = tmp_path / "manual.txt"
    path.write_text("报销需要主管审批。", encoding="utf-8")
    captured = []

    async def fake_embed_many(texts):
        return [[0.1, 0.2] for _ in texts]

    async def fake_upsert(points):
        captured.extend(points)

    monkeypatch.setattr(knowledge_service.embedding_service, "embed_texts", fake_embed_many)
    monkeypatch.setattr(knowledge_service.qdrant_service, "upsert_chunks", fake_upsert)

    result = await knowledge_service.index_document(
        path=path,
        original_name="员工手册.txt",
        kb_id="kb_default",
        tenant_id="tenant_default",
        permission="public",
    )

    assert result["status"] == "indexed"
    assert result["chunk_count"] == 1
    payload = captured[0]["payload"]
    assert set(payload) == {
        "text",
        "kb_id",
        "doc_id",
        "chunk_id",
        "file_name",
        "file_type",
        "page",
        "section",
        "tenant_id",
        "permission",
        "created_at",
    }
    assert payload["file_name"] == "员工手册.txt"
