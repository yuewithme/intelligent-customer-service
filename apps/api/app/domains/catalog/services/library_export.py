"""Export only reference content, never customer conversations or credentials."""

import hashlib
import json

from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings


CONTENT_TABLES = (
    "youzan_products", "youzan_product_skus", "youzan_product_knowledge",
    "care_manual_cards", "care_manual_product_links", "activities",
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
    media = [_public_item(item) for item in _load_items()]
    return {"version": 1, "tables": tables,
            "cases": {kind: len(rows) for kind, rows in cases.items()} if inventory else cases,
            "media": len(media) if inventory else media}
