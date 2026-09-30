import json

import httpx
from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.shared.schemas.common import AppError, ErrorCode

router = APIRouter()


@router.get("/api/library-resources")
async def resources(request: Request, query: str = ""):
    if request.headers.get("X-App-Id") != "sales-review" or not 1 <= len(query.strip()) <= 500:
        raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
    try:
        response = await request.app.state.platform_client.post(
            get_settings().platform_api_url.rstrip("/") + "/service/library-consumer",
            headers={key: request.headers.get(key, "") for key in ("X-App-Id", "X-App-Key")}, timeout=5)
        if response.status_code != 200 or response.json().get("project_id") != "p_lanhua":
            raise AppError(ErrorCode.PERMISSION_DENIED, status_code=403)
    except (httpx.HTTPError, ValueError):
        raise AppError(ErrorCode.INTERNAL_ERROR, "资料授权服务暂不可用", status_code=503) from None
    from app.domains.catalog.services.product_knowledge_service import list_product_knowledge, search_catalog_products
    from app.domains.sales.services.care_manual_service import test_match_care_manuals
    from app.domains.sales.services.activity_service import list_activities

    items = []
    products = list_product_knowledge(keyword=query, page_size=5)["items"] or search_catalog_products(query, limit=5)
    for product in products:
        identity = product.get('id') or product['item_id']
        items.append({"id": f"product-knowledge:{identity}", "title": product.get("product_name") or product["title"], "category": "product",
                      "content": json.dumps({"usage_note": "塑品话术仅作表达参考，价格库存以当前可售商品为准", **product}, ensure_ascii=False), "nature": "reference"})
    for manual in test_match_care_manuals(query=query, limit=5).get("matches", []):
        items.append({"id": f"care-manual:{manual['card_id']}", "title": manual.get("title", "养护手册"), "category": "product",
                      "content": json.dumps(manual, ensure_ascii=False), "nature": "reference"})
    for activity in list_activities(keyword=query, page_size=5, available_only=True)["items"]:
        items.append({"id": f"activity:{activity['id']}", "title": activity["title"], "category": "business",
                      "content": json.dumps(activity, ensure_ascii=False), "nature": "reference"})
    return {"items": items[:8]}
