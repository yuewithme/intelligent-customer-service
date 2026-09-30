"""Export only reference content, never customer conversations or credentials."""

import hashlib
import json

from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings


CONTENT_TABLES = (
    "youzan_products", "youzan_product_skus", "youzan_product_knowledge",
    "care_manual_cards", "care_manual_product_links",
    "youzan_product_sync_runs", "product_knowledge_imports", "care_manual_sync_runs",
    "orchid_categories", "orchid_varieties", "orchid_variety_traits",
    "orchid_value_points", "orchid_skus", "orchid_common_knowledge",
    "orchid_sales_copy", "orchid_hot_breakdowns", "orchid_knowledge_chunks",
    "prompt_blocks", "customer_level_profiles", "customer_level_prompt_bindings",
    "tag_prompt_bindings", "tag_categories", "tag_definitions",
)


def export_content(*, inventory: bool = True) -> dict:
    engine = create_engine(get_settings().database_url)
    tables = {}
    try:
        inspector = inspect(engine)
        available = set(inspector.get_table_names())
        with engine.connect() as conn:
            for name in CONTENT_TABLES:
                if name not in available:
                    tables[name] = {"count": 0, "missing": True}
                    continue
                columns = [c["name"] for c in inspector.get_columns(name) if c["name"] != "embedding_json"]
                keys = inspector.get_pk_constraint(name)["constrained_columns"]
                query = 'SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM "' + name + '"'
                if keys:
                    query += ' ORDER BY ' + ','.join('"' + c + '"' for c in keys)
                rows = [dict(row) for row in conn.execute(text(query)).mappings()]
                digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
                tables[name] = {"count": len(rows), "columns": columns, "primary_key": keys, "sha256": digest}
                if not inventory:
                    tables[name]["rows"] = rows
    finally:
        engine.dispose()
    from app.domains.decisioning.services.conversation_case_service import load_conversation_cases
    from app.domains.catalog.services.agent_media_library_service import _load_items, _public_item

    cases = {kind: load_conversation_cases(kind) for kind in ("complete", "cleaned")}
    media = [{**item, **_public_item(item)} for item in _load_items()]
    engine = create_engine(get_settings().chat_log_db_url)
    try:
        if "activities" in inspect(engine).get_table_names():
            with engine.connect() as conn:
                rows = [dict(r) for r in conn.execute(text("SELECT * FROM activities ORDER BY id")).mappings()]
            tables["activities"] = {"count": len(rows)}
            if not inventory:
                tables["activities"]["rows"] = rows
    finally:
        engine.dispose()
    settings = get_settings()
    return {"version": 1, "tables": tables,
            "catalog_config": {key: getattr(settings, key) for key in ("youzan_kdt_id", "youzan_product_page_path_template")},
            "cases": {kind: len(rows) for kind, rows in cases.items()} if inventory else cases,
            "media": len(media) if inventory else media}


async def export_search_documents(*, inventory: bool):
    from collections import Counter
    from app.domains.knowledge.services import qdrant_service
    if qdrant_service._is_memory_mode():
        return {} if inventory else []
    client = qdrant_service._client()
    settings = get_settings()
    try:
        if not await client.collection_exists(settings.qdrant_collection):
            return {} if inventory else []
        rows, offset = [], None
        while True:
            points, offset = await client.scroll(settings.qdrant_collection, limit=256, offset=offset, with_vectors=False)
            rows.extend({"point_id": str(p.id), **(p.payload or {})} for p in points)
            if offset is None:
                break
            if len(rows) > 100000:
                raise RuntimeError("知识索引规模超过本次导出上限，导出已停止")
        if inventory:
            return dict(Counter(str(row.get("kb_id")) + ":" + str(row.get("tenant_id")) + ":" + str(row.get("permission")) for row in rows))
        return rows
    finally:
        await client.close()


def freeze_content():
    """Retain the old rows as a rollback archive, preventing divergent edits."""
    import sqlite3
    from pathlib import Path
    from sqlalchemy.engine import make_url

    settings = get_settings()
    frozen = []
    for url, tables in (
        (settings.database_url, tuple(name for name in CONTENT_TABLES if name.startswith(("youzan_", "care_manual_", "orchid_")) or name == "product_knowledge_imports")),
        (settings.chat_log_db_url, ("activities",)),
    ):
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite":
            raise RuntimeError("迁移锁仅支持现有 SQLite 源")
        source = Path(parsed.database)
        backup = source.with_name(source.name + ".before-unified-content.sqlite3")
        with sqlite3.connect(source, timeout=30) as conn:
            available = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not backup.exists():
                with sqlite3.connect(backup) as target:
                    conn.backup(target)
                    assert target.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                backup.chmod(0o600)
            conn.execute("BEGIN IMMEDIATE")
            for table in tables:
                if table not in available:
                    continue
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "unified_source_{table}_{operation}" BEFORE {operation} ON "{table}"
                        BEGIN SELECT RAISE(ABORT, 'Content moved to unified platform'); END''')
                frozen.append(table)
            conn.commit()
    return {"frozen": frozen}
