import httpx

from app.core.config import get_settings


async def search_library(query: str, category: str = "all") -> dict:
    settings = get_settings()
    if len(settings.platform_shared_secret) < 32:
        return {"items": [], "unavailable": True}
    root = settings.platform_portal_url.rstrip("/").removesuffix("/portal")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            result = await client.get(root + "/review/api/library/resources", params={"query": query, "category": category},
                                      headers={"X-App-Id": "orchid-sales-agent", "X-App-Key": settings.platform_shared_secret})
            result.raise_for_status()
            return result.json()
    except (httpx.HTTPError, ValueError):
        return {"items": [], "unavailable": True}
