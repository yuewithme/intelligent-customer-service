import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base, OrchidKnowledgeChunkModel
from app.domains.catalog.orchid_products import knowledge_index
from app.services import rag_service


def _reset_db(monkeypatch, tmp_path):
    db_path = tmp_path / "orchid.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    knowledge_index._sessionmakers.clear()
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine, tables=[OrchidKnowledgeChunkModel.__table__])
    return sessionmaker(bind=engine)


@pytest.mark.asyncio
async def test_index_orchid_knowledge_chunks_persists_embeddings(monkeypatch, tmp_path):
    factory = _reset_db(monkeypatch, tmp_path)
    with factory() as session:
        session.add(
            OrchidKnowledgeChunkModel(
                source_table="orchid_common_knowledge",
                entity_type="common_knowledge",
                variety_name=None,
                category_name="建兰",
                chunk_type="养护",
                chunk_title="建兰养护",
                content="建兰喜通风散光，浇水要见干见湿。",
            )
        )
        session.commit()

    async def fake_embed_texts(texts):
        assert texts == ["建兰养护\n建兰喜通风散光，浇水要见干见湿。"]
        return [[0.1, 0.2]]

    async def fake_upsert(points):
        del points

    monkeypatch.setattr(
        knowledge_index.embedding_service,
        "embed_texts",
        fake_embed_texts,
    )
    monkeypatch.setattr(
        knowledge_index.qdrant_service,
        "upsert_chunks",
        fake_upsert,
    )

    try:
        result = await knowledge_index.index_orchid_knowledge_chunks()
        with factory() as session:
            row = session.query(OrchidKnowledgeChunkModel).one()
            stored = json.loads(row.embedding_json)
    finally:
        get_settings.cache_clear()
        knowledge_index._sessionmakers.clear()

    assert result == {"indexed": 1, "skipped": 0}
    assert stored == [0.1, 0.2]


@pytest.mark.asyncio
async def test_index_orchid_knowledge_chunks_upserts_qdrant_payload(monkeypatch, tmp_path):
    factory = _reset_db(monkeypatch, tmp_path)
    with factory() as session:
        session.add(
            OrchidKnowledgeChunkModel(
                source_table="orchid_value_points",
                entity_type="variety",
                variety_name="东方红荷",
                category_name="建兰",
                chunk_type="产品基础信息",
                chunk_title="东方红荷 - 产品基础信息",
                content="东方红荷花色喜庆，适合阳台养护。",
            )
        )
        session.commit()

    captured = []

    async def fake_embed_texts(texts):
        del texts
        return [[0.3, 0.4]]

    async def fake_upsert(points):
        captured.extend(points)

    monkeypatch.setattr(knowledge_index.embedding_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(knowledge_index.qdrant_service, "upsert_chunks", fake_upsert)

    try:
        result = await knowledge_index.index_orchid_knowledge_chunks()
    finally:
        get_settings.cache_clear()
        knowledge_index._sessionmakers.clear()

    assert result == {"indexed": 1, "skipped": 0}
    assert captured[0]["id"] == "orchid_chunk_1"
    assert captured[0]["vector"] == [0.3, 0.4]
    assert captured[0]["payload"]["kb_id"] == "kb_orchid_basic"
    assert captured[0]["payload"]["file_name"] == "兰花产品知识库"
    assert captured[0]["payload"]["variety_name"] == "东方红荷"
    assert captured[0]["payload"]["source_table"] == "orchid_value_points"
    assert captured[0]["payload"]["entity_type"] == "variety"


@pytest.mark.asyncio
async def test_index_orchid_knowledge_chunks_syncs_existing_embeddings(monkeypatch, tmp_path):
    factory = _reset_db(monkeypatch, tmp_path)
    with factory() as session:
        session.add(
            OrchidKnowledgeChunkModel(
                source_table="orchid_value_points",
                entity_type="variety",
                variety_name="东方红荷",
                category_name="建兰",
                chunk_type="产品基础信息",
                chunk_title="东方红荷 - 产品基础信息",
                content="东方红荷花色喜庆，适合阳台养护。",
                embedding_json=json.dumps([0.5, 0.6]),
            )
        )
        session.commit()

    captured = []

    async def fail_embed_texts(texts):
        del texts
        raise AssertionError("existing embeddings should not be regenerated")

    async def fake_upsert(points):
        captured.extend(points)

    monkeypatch.setattr(knowledge_index.embedding_service, "embed_texts", fail_embed_texts)
    monkeypatch.setattr(knowledge_index.qdrant_service, "upsert_chunks", fake_upsert)

    try:
        result = await knowledge_index.index_orchid_knowledge_chunks(sync_existing=True)
    finally:
        get_settings.cache_clear()
        knowledge_index._sessionmakers.clear()

    assert result == {"indexed": 0, "skipped": 0}
    assert captured[0]["id"] == "orchid_chunk_1"
    assert captured[0]["vector"] == [0.5, 0.6]


@pytest.mark.asyncio
async def test_rag_chat_searches_persisted_orchid_knowledge(monkeypatch, tmp_path):
    factory = _reset_db(monkeypatch, tmp_path)
    with factory() as session:
        session.add(
            OrchidKnowledgeChunkModel(
                source_table="orchid_common_knowledge",
                entity_type="common_knowledge",
                variety_name=None,
                category_name="建兰",
                chunk_type="养护",
                chunk_title="建兰养护",
                content="建兰喜通风散光，浇水要见干见湿。",
                embedding_json=json.dumps([1.0, 0.0]),
            )
        )
        session.commit()

    async def fake_embed(text):
        assert text == "建兰怎么浇水？"
        return [1.0, 0.0]

    async def fake_qdrant_search(vector, **filters):
        del vector, filters
        return []

    async def fake_generate(prompt, *, purpose):
        assert "建兰喜通风散光" in prompt
        assert purpose == "rag_fast"
        return {"answer": "建兰放通风散光处，浇水见干见湿。", "usage": {}}

    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fake_qdrant_search)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    try:
        result = await rag_service.rag_chat(
            user_id="user_001",
            message="建兰怎么浇水？",
            kb_id="kb_orchid_basic",
            metadata={"tenant_id": "tenant_default", "permission": "public"},
        )
    finally:
        get_settings.cache_clear()
        knowledge_index._sessionmakers.clear()

    assert result["answer"] == "建兰放通风散光处，浇水见干见湿。"
    assert result["sources"][0]["doc_id"].startswith("orchid_chunk_")
    assert result["sources"][0]["file_name"] == "兰花产品知识库"
