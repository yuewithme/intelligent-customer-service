import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.core.config import get_settings


BEGINNER_CARE_LIBRARY = "beginner_care"
BEGINNER_CARE_TAGS = frozenset({"L1 青铜期", "L2 白银期"})
COLLECTION_NAME = "l1-l2-orchid-care"
COLLECTION_DIR = Path(__file__).resolve().parents[3] / "static" / COLLECTION_NAME


@lru_cache(maxsize=1)
def _topics() -> dict:
    manifest = COLLECTION_DIR / "manifest.json"
    if not manifest.is_file():
        return {}
    rows = json.loads(manifest.read_text(encoding="utf-8"))["topics"]
    return {
        row["id"]: row for row in rows
        if row["images"] and all((COLLECTION_DIR / image["path"]).is_file() for image in row["images"])
    }


def list_beginner_care_topics() -> list[dict]:
    return [{"id": row["id"], "name": row["title"], "category": "", "description": ""}
            for row in _topics().values()]


def get_beginner_care_topic(item_id: str) -> dict | None:
    topic = _topics().get(item_id)
    if not topic:
        return None
    settings = get_settings()
    base_url = (settings.platform_app_origin or settings.app_public_base_url).rstrip("/")
    if not base_url:
        return None
    prefix = f"{base_url}/static/{COLLECTION_NAME}/"
    return {
        "title": topic["title"],
        "content_reference": {"library": BEGINNER_CARE_LIBRARY, "item_id": item_id},
        "outbound_messages": [{"type": "image", "content": prefix + quote(image["path"], safe="/")}
                              for image in topic["images"]],
    }


def daily_beginner_care_topic(local_date: date) -> dict | None:
    ids = tuple(_topics())
    return get_beginner_care_topic(ids[local_date.toordinal() % len(ids)]) if ids else None
