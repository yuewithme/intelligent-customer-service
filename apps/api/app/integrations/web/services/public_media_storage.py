"""COS storage for business files explicitly approved for public CDN access."""

import asyncio
import logging
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlsplit

from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.operations.cos_files import (
    MAX_BATCH_BYTES, MEDIA_DIRECTORIES, STATE_DIRECTORY, SafetyError, category_for,
    create_client, digest, fingerprint, load_json, private_directory, require, upload_one, verified_public_record,
)


logger = logging.getLogger("wechat_rag_bot.cos")


def public_file_item(path: Path) -> tuple[Path, dict]:
    root = Path(get_settings().upload_dir).absolute().parent.resolve()
    relative = path.absolute().relative_to(root)
    category, eligible = category_for(
        relative, include_customer_files=get_settings().cos_customer_files_public_enabled
    )
    require(eligible, "not approved public media")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_BATCH_BYTES,
            "invalid public media file")
    return root, {"id": digest(relative.as_posix().encode()), "path": relative.as_posix(),
                  "category": category, **fingerprint(info)}


def indexed_public_url(path: Path) -> str | None:
    settings = get_settings()
    if not settings.cos_public_media_enabled:
        return None
    try:
        root, item = public_file_item(path)
        record = verified_public_record(root, item, settings)
        return record["url"] if record else None
    except (OSError, ValueError, KeyError, TypeError, SafetyError):
        return None


def publish_public_file(path: Path) -> str:
    root, item = public_file_item(path)
    state = private_directory(root / STATE_DIRECTORY)
    result = upload_one(create_client(get_settings()), get_settings(), state, root, item)
    return result["url"]


def public_media_url_sync(path: Path, local_url: str) -> str:
    if not get_settings().cos_public_media_enabled:
        return local_url
    existing = indexed_public_url(path)
    if existing:
        return existing
    try:
        return publish_public_file(path)
    except Exception as error:
        # Preserve the existing public endpoint while the worker retries; never log SDK exceptions.
        logger.warning("COS public upload pending error=%s", type(error).__name__)
        return local_url


async def public_media_url(path: Path, local_url: str) -> str:
    if not get_settings().cos_public_media_enabled:
        return local_url
    try:
        return await asyncio.wait_for(asyncio.to_thread(public_media_url_sync, path, local_url), timeout=10)
    except asyncio.TimeoutError:
        # The upload thread retains its checkpoint and completes independently of the request budget.
        logger.warning("COS public upload continuing in background")
        return local_url


def local_public_media_path(url: str) -> Path | None:
    settings = get_settings()
    if not settings.cos_public_media_enabled:
        return None
    parsed, origin = urlsplit(url), urlsplit(settings.cos_cdn_base_url)
    prefix = "/intelligent-customer-service/assets/"
    if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or not parsed.path.startswith(prefix):
        return None
    key = parsed.path[len(prefix):]
    if not re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{32}\.[a-z0-9]+", key):
        return None
    try:
        root = Path(settings.upload_dir).absolute().parent.resolve()
        record = load_json(root / STATE_DIRECTORY / "public-objects" / (key + ".json"))
        path = root / record["source"]["path"]
        require(path.resolve().is_relative_to(root), "invalid public media object")
        if indexed_public_url(path) == parsed._replace(query="", fragment="").geturl():
            return path
    except (OSError, ValueError, KeyError, TypeError, SafetyError):
        pass
    return None


class PublicMediaFiles(StaticFiles):
    async def get_response(self, path, scope):
        if scope["method"] in {"GET", "HEAD"} and get_settings().cos_public_media_enabled:
            full_path, info = await asyncio.to_thread(self.lookup_path, path)
            if info and stat.S_ISREG(info.st_mode):
                url = await asyncio.to_thread(indexed_public_url, Path(full_path))
                if url:
                    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
        return await super().get_response(path, scope)


def pending_public_files() -> list[Path]:
    upload_dir = Path(get_settings().upload_dir)
    pending = []
    now = time.time()
    directories_to_scan = ([upload_dir.parent / "media", upload_dir]
                           if get_settings().cos_customer_files_public_enabled
                           else [upload_dir / name for name in MEDIA_DIRECTORIES])
    for directory in directories_to_scan:
        if directory.is_symlink():
            continue
        for current, directories, files in os.walk(directory, followlinks=False):
            directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
            for name in files:
                path = Path(current) / name
                try:
                    public_file_item(path)
                    if now - path.lstat().st_mtime < 10 or indexed_public_url(path):
                        continue
                except (OSError, ValueError, SafetyError):
                    continue
                pending.append(path)
                if len(pending) >= 20:
                    return pending
    return pending


async def public_media_worker(stop_event: asyncio.Event):
    while not stop_event.is_set():
        if get_settings().cos_public_media_enabled:
            try:
                for path in await asyncio.to_thread(pending_public_files):
                    if stop_event.is_set():
                        break
                    await asyncio.to_thread(publish_public_file, path)
            except Exception as error:
                logger.warning("COS public sync deferred error=%s", type(error).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
