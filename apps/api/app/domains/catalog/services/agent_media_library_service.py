from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

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


def search_agent_media(
    query: str, *, category: str = "", limit: int = 5
) -> list[dict[str, Any]]:
    normalized_query = _normalize(query)
    if not normalized_query:
        return []
    normalized_category = _normalize(category)
    terms = _query_terms(normalized_query)
    matches: list[tuple[int, dict[str, Any]]] = []
    for item in _load_items():
        if (
            normalized_category
            and _normalize(str(item.get("category") or ""))
            != normalized_category
        ):
            continue
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
    _read_remote_manifest.cache_clear()


def _load_items() -> tuple[dict[str, Any], ...]:
    remote_base_url = get_settings().agent_media_library_base_url.strip().rstrip("/")
    if remote_base_url:
        return _read_remote_manifest(remote_base_url, int(time.time() // 60))
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
    return _parse_rows(rows, root=root, asset_base_url=_local_asset_base_url())


@lru_cache(maxsize=4)
def _read_remote_manifest(
    base_url: str, cache_minute: int
) -> tuple[dict[str, Any], ...]:
    del cache_minute
    try:
        response = httpx.get(
            f"{base_url}/manifest.json",
            timeout=10,
            follow_redirects=True,
        )
        response.raise_for_status()
        rows = json.loads(response.content.decode("utf-8-sig"))
    except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    return _parse_rows(rows, root=None, asset_base_url=base_url)


def _parse_rows(
    rows: Any, *, root: Path | None, asset_base_url: str
) -> tuple[dict[str, Any], ...]:
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
        asset_path = _safe_relative_path(f"{category}/{relative_path}")
        if not asset_path:
            continue
        media_path = _safe_path(root, asset_path) if root is not None else None
        if root is not None and (media_path is None or not media_path.is_file()):
            continue
        suffix = Path(relative_path).suffix.lower()
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
        thumbnail_relative = _safe_relative_path(thumbnail_path)
        thumbnail = (
            _safe_path(root, thumbnail_relative)
            if root is not None and thumbnail_relative
            else None
        )
        if root is not None and thumbnail_relative and not thumbnail.is_file():
            thumbnail_relative = ""
        items.append(
            {
                "id": digest[:24],
                "category": category,
                "relative_path": relative_path,
                "title": Path(relative_path).stem,
                "media_type": media_type,
                "bytes": int(
                    row.get("bytes")
                    or (media_path.stat().st_size if media_path is not None else 0)
                ),
                "thumbnail_path": thumbnail_relative,
                "asset_base_url": asset_base_url,
            }
        )
    return tuple(items)


def _safe_relative_path(relative_path: str) -> str:
    normalized = relative_path.strip().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if (
        not parts
        or normalized.startswith("/")
        or any(part in {".", ".."} for part in parts)
    ):
        return ""
    return "/".join(parts)


def _safe_path(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    base_url = str(item.get("asset_base_url") or "").rstrip("/")
    category = str(item["category"])
    relative_path = str(item["relative_path"])
    url = _asset_url(base_url, f"{category}/{relative_path}")
    thumbnail_path = str(item.get("thumbnail_path") or "")
    return {
        "material_ref": f"material:{MATERIAL_REF_PREFIX}{item['id']}",
        "title": item["title"],
        "category": category,
        "format": item["media_type"],
        "bytes": item["bytes"],
        "url": url,
        "thumb_url": _asset_url(base_url, thumbnail_path) if thumbnail_path else "",
        "access": "customer_service_agent",
        "match_reason": f"{category}素材库匹配",
    }


def _local_asset_base_url() -> str:
    base_url = get_settings().app_public_base_url.strip().rstrip("/")
    return (
        f"{base_url}/static/{LIBRARY_DIR_NAME}"
        if base_url
        else f"/static/{LIBRARY_DIR_NAME}"
    )


def _asset_url(base_url: str, relative_path: str) -> str:
    encoded = "/".join(quote(part) for part in relative_path.split("/") if part)
    return f"{base_url}/{encoded}"


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
