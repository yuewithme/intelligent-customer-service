from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import get_settings


LIBRARY_DIR_NAME = "agent-material-library"
MATERIAL_REF_PREFIX = "agent-media:"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
_GENERIC_QUERY_WORDS = (
    "图文解说",
    "图文",
    "知识",
    "教程",
    "资料",
    "视频",
    "兰花",
    "养兰",
    "怎么",
    "如何",
)


def agent_media_library_dir() -> Path:
    return Path(get_settings().upload_dir) / LIBRARY_DIR_NAME


def search_agent_media(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    normalized_query = _normalize(query)
    if not normalized_query:
        return []
    terms = _query_terms(normalized_query)
    matches: list[tuple[int, dict[str, Any]]] = []
    for item in _load_items():
        haystack = _normalize(
            " ".join(
                (
                    str(item.get("category") or ""),
                    str(item.get("title") or ""),
                    str(item.get("relative_path") or ""),
                )
            )
        )
        score = 0
        if normalized_query in haystack:
            score += 100
        for term in terms:
            if term in haystack:
                score += max(5, len(term) * 3)
        if score:
            matches.append((score, item))
    matches.sort(
        key=lambda pair: (
            -pair[0],
            str(pair[1].get("category") or ""),
            str(pair[1].get("relative_path") or ""),
        )
    )
    return [_public_item(item) for _, item in matches[: max(1, min(limit, 20))]]


def get_agent_media(material_id: str) -> dict[str, Any] | None:
    wanted = material_id.removeprefix(MATERIAL_REF_PREFIX).strip().lower()
    if not wanted:
        return None
    for item in _load_items():
        if str(item.get("id") or "").lower() == wanted:
            return _public_item(item)
    return None


def reset_agent_media_library_cache() -> None:
    _read_manifest.cache_clear()


def _load_items() -> tuple[dict[str, Any], ...]:
    manifest = agent_media_library_dir() / "manifest.json"
    if not manifest.is_file():
        return ()
    return _read_manifest(str(manifest), manifest.stat().st_mtime_ns)


@lru_cache(maxsize=4)
def _read_manifest(path: str, modified_ns: int) -> tuple[dict[str, Any], ...]:
    del modified_ns
    root = Path(path).parent.resolve()
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(rows, list):
        return ()
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "").strip()
        relative_path = (
            str(row.get("relative_path") or "").strip().replace("\\", "/")
        )
        digest = str(row.get("sha256") or "").strip().lower()
        if not category or not relative_path or len(digest) < 16:
            continue
        media_path = _safe_path(root, f"{category}/{relative_path}")
        if media_path is None or not media_path.is_file():
            continue
        suffix = media_path.suffix.lower()
        media_type = (
            "image"
            if suffix in IMAGE_SUFFIXES
            else "video"
            if suffix in VIDEO_SUFFIXES
            else ""
        )
        if not media_type:
            continue
        thumbnail_path = (
            str(row.get("thumbnail_path") or "").strip().replace("\\", "/")
        )
        thumbnail = _safe_path(root, thumbnail_path) if thumbnail_path else None
        items.append(
            {
                "id": digest[:24],
                "category": category,
                "relative_path": relative_path,
                "title": media_path.stem,
                "media_type": media_type,
                "bytes": int(row.get("bytes") or media_path.stat().st_size),
                "thumbnail_path": (
                    thumbnail_path if thumbnail and thumbnail.is_file() else ""
                ),
            }
        )
    return tuple(items)


def _safe_path(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    base_url = settings.app_public_base_url.strip().rstrip("/")
    category = str(item["category"])
    relative_path = str(item["relative_path"])
    url = _public_url(base_url, f"{category}/{relative_path}")
    thumbnail_path = str(item.get("thumbnail_path") or "")
    return {
        "material_ref": f"material:{MATERIAL_REF_PREFIX}{item['id']}",
        "title": item["title"],
        "category": category,
        "format": item["media_type"],
        "bytes": item["bytes"],
        "url": url,
        "thumb_url": _public_url(base_url, thumbnail_path) if thumbnail_path else "",
        "access": "customer_service_agent",
        "match_reason": f"{category}素材库匹配",
    }


def _public_url(base_url: str, relative_path: str) -> str:
    encoded = "/".join(quote(part) for part in relative_path.split("/") if part)
    path = f"/static/{LIBRARY_DIR_NAME}/{encoded}"
    return f"{base_url}{path}" if base_url else path


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).casefold())


def _query_terms(normalized_query: str) -> tuple[str, ...]:
    reduced = normalized_query
    for word in _GENERIC_QUERY_WORDS:
        reduced = reduced.replace(_normalize(word), " ")
    terms = [term for term in reduced.split() if len(term) >= 2]
    if normalized_query not in terms:
        terms.append(normalized_query)
    return tuple(dict.fromkeys(terms))
