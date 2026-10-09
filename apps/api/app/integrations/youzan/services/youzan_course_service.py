from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx


def course_source(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https" or not (parsed.hostname or "").endswith(".youzan.com")
        or parsed.username or parsed.password or parsed.port not in (None, 443)
    ):
        raise ValueError("请填写有赞课程的 HTTPS 分享链接")
    query = parse_qs(parsed.query)
    match = re.fullmatch(r"/wscvis/course/detail/([a-z0-9]{8,64})/?", parsed.path)
    alias = match.group(1) if match else query.get("alias", [""])[0]
    if not match and (parsed.path != "/wscvis/knowledge/index" or query.get("page") != ["columnshow"]):
        raise ValueError("请填写有赞专栏分享链接")
    kdt_id = query.get("kdt_id", [""])[0]
    if not re.fullmatch(r"[a-z0-9]{8,64}", alias) or not kdt_id.isdigit() or int(kdt_id) <= 0:
        raise ValueError("课程链接缺少专栏别名或店铺编号")
    return alias, int(kdt_id)


def parse_course_page(html: str, *, alias: str, kdt_id: int) -> dict[str, Any]:
    try:
        match = re.search(r"window\._global\s*=\s*", html)
        if match is None:
            raise ValueError("missing course data")
        page = json.JSONDecoder().raw_decode(html[match.end():])[0]
        encoded = page["goodsData"]
        if isinstance(encoded, str):
            # Youzan embeds this field using JavaScript escape(), including %uXXXX.
            encoded = re.sub(r"%u([0-9a-fA-F]{4})", lambda m: "\\u" + m.group(1), encoded)
            course = json.loads(unquote(encoded, encoding="latin-1"))
        else:
            course = encoded
        if not isinstance(course, dict) or course.get("alias") != alias or course.get("kdtId") != kdt_id:
            raise ValueError("course identity mismatch")
        if type(course.get("id")) is not int or course["id"] <= 0 or not str(course.get("title") or "").strip():
            raise ValueError("missing course identity")
        if type(course.get("status")) is not int or course["status"] not in (0, 1, 2, 3):
            raise ValueError("unknown course status")
        price = course.get("price")
        price = price if type(price) is int and price >= 0 else None
        count = course.get("contentsCount")
        count = count if type(count) is int and count >= 0 else None
        available = course["status"] == 1 and not course.get("isLock") and not course.get("access")
        return {
            "note_id": str(course["id"]), "note_alias": alias,
            "title": str(course["title"]).strip(),
            "note_url": f"https://h5.youzan.com/wscvis/course/detail/{alias}?kdt_id={kdt_id}",
            "cover_url": str(course.get("cover") or ""),
            "note_status": "published" if available else "unavailable",
            "price_cent": price, "content_count": count,
            "publish_time": course.get("publishAt"),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("有赞课程信息不完整，已保留原数据") from exc


async def fetch_courses(urls: list[str], *, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    sources = list(dict.fromkeys(course_source(url) for url in urls))
    if client is None:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as managed:
            return await fetch_courses(urls, client=managed)
    semaphore = asyncio.Semaphore(3)

    async def fetch(alias: str, kdt_id: int) -> dict[str, Any]:
        async with semaphore:
            url = f"https://h5.youzan.com/wscvis/course/detail/{alias}?kdt_id={kdt_id}"
            for _ in range(4):
                response = await client.get(url, follow_redirects=False)
                if not response.is_redirect:
                    break
                redirected = urljoin(url, response.headers.get("location", ""))
                # Public course pages redirect to the owning shop's Youzan domain.
                if course_source(redirected) != (alias, kdt_id):
                    raise ValueError("有赞课程跳转不匹配，已保留原数据")
                url = redirected
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError("有赞课程响应过大，已保留原数据")
            return parse_course_page(response.text, alias=alias, kdt_id=kdt_id)

    return list(await asyncio.gather(*(fetch(*source) for source in sources)))


async def verify_free_course(record: dict[str, Any]) -> dict[str, Any] | None:
    if not record.get("available") or record.get("price_cent") != 0:
        return None
    try:
        course = (await fetch_courses([record["note_url"]]))[0]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    if course["note_status"] != "published" or course["price_cent"] != 0:
        return None
    if record["youzan_note_id"] != "course:" + course["note_id"]:
        return None
    return {**record, "title": course["title"], "note_url": course["note_url"], "cover_url": course["cover_url"]}
