import json
import re
from pathlib import Path

from sqlalchemy import select

from app.infrastructure.database.models import ProductKnowledgeImportModel, YouzanProductKnowledgeModel
from app.infrastructure.database.product_store import get_product_session
from app.domains.catalog.services.product_knowledge_service import ORCHID_CATEGORIES, _auto_link, _now, _normalize_name, _split_aliases


VERSION = "orchid-recommendation-20260908"


def _primary_name(value: str) -> str:
    return _normalize_name(re.sub(r"[（(][^）)]*[）)]", "", value))


def _name_keys(name: str, aliases: str | None) -> set[str]:
    primary = _primary_name(name)
    keys = {primary, *_split_aliases(aliases)}
    keys.update(_normalize_name(value) for value in re.findall(r"[（(]([^）)]+)[）)]", name))
    for category in ORCHID_CATEGORIES:
        if primary.startswith(category) and len(primary) > len(category):
            keys.add(primary[len(category):])
    return keys - {""}


def import_recommendation_catalog() -> int:
    """Apply the approved business table once, preserving later admin edits."""
    records = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "orchid_recommendation_20260908.json").read_text(encoding="utf-8")
    )
    with get_product_session() as session:
        if session.get(ProductKnowledgeImportModel, VERSION):
            return 0
        existing = list(session.scalars(select(YouzanProductKnowledgeModel)))
        claimed: set[int] = set()
        now = _now()
        for record in records:
            names = _name_keys(record["product_name"], record["aliases"])
            available = [row for row in existing if id(row) not in claimed]
            exact = [row for row in available if _primary_name(row.product_name) == _primary_name(record["product_name"])]
            if record["product_name"] == "红草":
                # The approved table treats 建兰红草 as distinct from 红草红荷 and 寒兰红草.
                matches = exact
            elif record["product_name"] == "蕙兰虎斑":
                matches = exact or [row for row in available if row.product_name == "虎斑"]
            else:
                matches = exact or [row for row in available if names.intersection(_name_keys(row.product_name, row.aliases))]
            if len(matches) > 1:
                raise ValueError(f"适配表存在多个同名或别名知识记录，请先合并：{record['product_name']}")
            row = matches[0] if matches else YouzanProductKnowledgeModel(created_at=now, updated_at=now)
            if not matches:
                session.add(row)
            claimed.add(id(row))
            aliases = list(dict.fromkeys([*_split_aliases(row.aliases), *_split_aliases(record["aliases"])]))
            if record["product_name"] == "红草红荷":
                aliases = [alias for alias in aliases if alias != "红草"]
            for field, value in record.items():
                setattr(row, field, value)
            row.aliases = "，".join(aliases)
            row.updated_at = now
        session.flush()
        _auto_link(session)
        session.add(ProductKnowledgeImportModel(version=VERSION, imported_at=now))
        session.commit()
    return len(records)
