from concurrent.futures import ThreadPoolExecutor

from app.domains.catalog.services.agent_media_library_service import MEDIA_CONTENT_CATEGORIES, list_agent_content
from app.domains.catalog.services.beginner_care_collection import BEGINNER_CARE_LIBRARY, list_beginner_care_topics
from app.domains.orchestration.schemas.canvas_workflow import CanvasDefinition, CanvasIssue
from app.domains.sales.services.activity_service import list_activities
from app.domains.sales.services.care_manual_service import list_care_manuals
from app.shared.schemas.common import AppError, ErrorCode


CONTENT_LIBRARIES = (
    ("care_manual", "养护手册", "篇", "按客户已购品种或明确的养护对象匹配手册。"),
    ("cloud_video", "云端视频", "个视频", "按分类匹配视频，使用素材配套文案。"),
    ("cloud_image", "云端图文", "张图文", "按分类匹配图片，使用素材配套文案。"),
    (BEGINNER_CARE_LIBRARY, "L1/L2 养护图文", "个专题", "面向 L1 青铜期和 L2 白银期客户，按专题发送整组图片。"),
    ("care_course", "视频课程", "个课程", "仅使用已启用、已发布且免费的课程。"),
    ("sales_activity", "销售活动", "个活动", "仅使用有效期内、已启用且允许 AI 发送的活动，保留活动原有发送规则。"),
)


def list_content_sources(libraries: set[str] | None = None) -> list[dict]:
    selected = [item for item in CONTENT_LIBRARIES if libraries is None or item[0] in libraries]
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending = [pool.submit(_content_source, *item) for item in selected]
        return [future.result() for future in pending]


def _all_items(list_items, **filters) -> list[dict]:
    items, page = [], 1
    while True:
        result = list_items(page=page, page_size=200, **filters)
        items.extend(result["items"])
        if len(items) >= result["total"]:
            return items
        if not result["items"]:
            raise AppError(ErrorCode.INTERNAL_ERROR, "内容目录尚未读取完整", status_code=503)
        page += 1


def _content_source(library: str, name: str, unit: str, description: str) -> dict:
    source = {"id": library, "name": name, "unit": unit, "description": description,
              "available": True, "count": 0, "categories": [], "items": [], "message": ""}
    if library in {"cloud_video", "cloud_image"}:
        source["categories"] = [{**category, "count": None} for category in MEDIA_CONTENT_CATEGORIES]
    try:
        if library in {"care_manual", "care_course"}:
            kind = "manual" if library == "care_manual" else "course"
            rows = _all_items(list_care_manuals, source_type=kind, enabled=True)
            source["items"] = [
                {"id": str(item["id"]), "name": item["title"], "category": "", "description": item.get("orchid_name") or ""}
                for item in rows if item.get("source_type") == kind and item.get("auto_send_allowed")
                and (kind == "manual" or item.get("price_cent") == 0)
            ]
        elif library == "sales_activity":
            rows = _all_items(list_activities, available_only=True)
            source["items"] = [
                {"id": str(item["id"]), "name": item["title"], "category": "", "description": item.get("summary") or ""}
                for item in rows if item.get("enabled") and item.get("ai_enabled")
                and item.get("effective_status") == "active" and item.get("item_count", 0) > 0
            ]
        elif library == BEGINNER_CARE_LIBRARY:
            source["items"] = list_beginner_care_topics()
        else:
            source.update(list_agent_content("video" if library == "cloud_video" else "image"))
        source["count"] = len(source["items"])
        if not source["count"]:
            source["message"] = f"{name}暂无可用内容，请先在资料库中准备内容。"
    except AppError:
        source.update(available=False, count=None, items=[], message=f"{name}暂时无法加载，请稍后重试。")
    return source


def resolve_content_sources(flow: CanvasDefinition, catalog: list[dict] | None = None) -> tuple[list[CanvasIssue], dict]:
    nodes = [node for node in flow.nodes if node.type == "scheduled_send" and node.content_source]
    if not nodes:
        return [], {}
    sources = {source["id"]: source for source in (catalog if catalog is not None else list_content_sources({node.content_source.library for node in nodes}))}
    issues, resolved = [], {}
    for node in nodes:
        selected = node.content_source
        source = sources[selected.library]
        message = source["message"]
        eligible = {item["id"]: item for item in source["items"] if not selected.category or item["category"] == selected.category}
        if source["available"] and source["count"]:
            if selected.category and not eligible:
                message = f"{source['name']}分类「{selected.category}」已不存在或暂无可用内容。"
            elif selected.selection == "specific" and any(item_id not in eligible for item_id in selected.item_ids):
                message = "指定内容已失效、不允许自动发送，或不属于当前内容库与分类。"
        if message:
            issues.append(CanvasIssue(node_id=node.id, field="content_source", message=message,
                                      suggestion="刷新内容库后重新选择可用内容；资料库恢复或补充内容后再发布。"))
        else:
            resolved[node.id] = {"library_name": source["name"], "description": source["description"],
                                 "items": [eligible[item_id] for item_id in selected.item_ids]}
    return issues, resolved
