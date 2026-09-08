from __future__ import annotations

import re
from functools import lru_cache

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    YouzanProductKnowledgeModel,
    YouzanProductModel,
    YouzanProductSkuModel,
    YouzanProductSyncRunModel,
    ProductKnowledgeImportModel,
)


_TABLES = [
    YouzanProductModel.__table__,
    YouzanProductSkuModel.__table__,
    YouzanProductSyncRunModel.__table__,
    YouzanProductKnowledgeModel.__table__,
    ProductKnowledgeImportModel.__table__,
]


@lru_cache
def _session_factory(database_url: str):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=_TABLES)
    if "alias" not in {
        column["name"] for column in inspect(engine).get_columns("youzan_products")
    }:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE youzan_products ADD COLUMN alias VARCHAR(128)")
            )
    _ensure_product_knowledge_aliases(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("youzan_product_knowledge")}
    with engine.begin() as connection:
        for name, sql_type in (
            ("demand_tags", "JSON"),
            ("seeding_scene", "VARCHAR(128)"),
            ("source_demand", "VARCHAR(256)"),
            ("spec_hint", "VARCHAR(128)"),
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE youzan_product_knowledge ADD COLUMN {name} {sql_type}"))
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _ensure_product_knowledge_aliases(engine) -> None:
    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("youzan_product_knowledge")
    }
    if "aliases" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE youzan_product_knowledge ADD COLUMN aliases TEXT")
            )

    if "orchid_varieties" not in set(inspector.get_table_names()):
        return

    with engine.begin() as connection:
        legacy_rows = connection.execute(
            text(
                "SELECT variety_name, primary_alias, aliases_text "
                "FROM orchid_varieties"
            )
        ).mappings()
        legacy_aliases: dict[str, list[str]] = {}
        for row in legacy_rows:
            key = _normalize_product_name(row["variety_name"])
            if not key:
                continue
            values = legacy_aliases.setdefault(key, [])
            for value in (row["primary_alias"], row["aliases_text"]):
                values.extend(_alias_values(value))

        knowledge_rows = list(connection.execute(
            text("SELECT id, product_name, aliases FROM youzan_product_knowledge")
        ).mappings())
        product_names = {_normalize_product_name(row["product_name"]) for row in knowledge_rows}
        for row in knowledge_rows:
            product_name = str(row["product_name"] or "").strip()
            candidates = [
                *_alias_values(row["aliases"]),
                *legacy_aliases.get(_normalize_product_name(product_name), []),
            ]
            aliases = []
            seen = {_normalize_product_name(product_name)}
            for alias in candidates:
                normalized = _normalize_product_name(alias)
                if not normalized or normalized in seen:
                    continue
                # A separately named product in the current catalog is not a legacy alias.
                if normalized in product_names:
                    continue
                seen.add(normalized)
                aliases.append(alias)
            value = "，".join(aliases)
            if value != str(row["aliases"] or "").strip():
                connection.execute(
                    text(
                        "UPDATE youzan_product_knowledge "
                        "SET aliases = :aliases WHERE id = :id"
                    ),
                    {"aliases": value or None, "id": row["id"]},
                )


def _alias_values(value: str | None) -> list[str]:
    aliases = []
    for item in re.split(r"[\s,，、;；/|]+|或", str(value or "")):
        alias = item.strip().strip("‘’“”\"'")
        if not alias or alias in {"无", "暂无", "未知", "不详", "待补充", "无别名"}:
            continue
        if any(marker in alias for marker in ("文献", "资料", "别名", "待补", "未确认")):
            continue
        if len(alias) > 32:
            continue
        aliases.append(alias)
    return aliases


def _normalize_product_name(value: str | None) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).lower()


def get_product_session() -> Session:
    return _session_factory(get_settings().database_url)()


def reset_product_store_for_tests() -> None:
    _session_factory.cache_clear()
