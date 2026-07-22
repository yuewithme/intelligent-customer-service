from pathlib import Path
from types import SimpleNamespace

import pytest

from app.shared.schemas.common import AppError, ErrorCode
from app.services import knowledge_service


def test_chunk_text_uses_overlap():
    chunks = knowledge_service.chunk_text("abcdefghij", chunk_size=6, overlap=2)
    assert chunks == ["abcdef", "efghij"]


def test_split_markdown_sections_tracks_heading_hierarchy():
    markdown = """文档说明

# 入职流程
办理入职。

## 所需材料
准备身份证。

# 报销流程
提交报销单。
"""

    sections = knowledge_service.split_markdown_sections(markdown)

    assert [section["section"] for section in sections] == [
        None,
        "入职流程",
        "入职流程 / 所需材料",
        "报销流程",
    ]
    assert sections[2]["text"].startswith("## 所需材料")


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


@pytest.mark.asyncio
async def test_adaptive_markdown_index_sets_section_payload(monkeypatch, tmp_path):
    path = tmp_path / "manual.md"
    path.write_text(
        "# 入职流程\n办理入职。\n\n## 所需材料\n准备身份证。\n",
        encoding="utf-8",
    )
    captured = []

    async def fake_embed_many(texts):
        return [[0.1, 0.2] for _ in texts]

    async def fake_upsert(points):
        captured.extend(points)

    monkeypatch.setattr(
        knowledge_service,
        "get_settings",
        lambda: SimpleNamespace(
            chunk_size=600,
            chunk_overlap=100,
            chunk_strategy="adaptive",
            markdown_heading_max_level=6,
        ),
    )
    monkeypatch.setattr(
        knowledge_service.embedding_service, "embed_texts", fake_embed_many
    )
    monkeypatch.setattr(
        knowledge_service.qdrant_service, "upsert_chunks", fake_upsert
    )

    result = await knowledge_service.index_document(
        path=path,
        original_name="员工手册.md",
        kb_id="kb_default",
        tenant_id="tenant_default",
        permission="public",
    )

    assert result["chunk_count"] == 2
    assert [point["payload"]["section"] for point in captured] == [
        "入职流程",
        "入职流程 / 所需材料",
    ]
