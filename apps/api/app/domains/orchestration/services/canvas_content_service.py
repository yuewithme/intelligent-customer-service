from concurrent.futures import ThreadPoolExecutor

from app.domains.catalog.services.agent_media_library_service import VIDEO_CONTENT_CATEGORIES, list_agent_video_categories
from app.domains.orchestration.schemas.canvas_workflow import CanvasDefinition, CanvasIssue
from app.domains.sales.services.care_manual_service import list_care_manuals
from app.shared.schemas.common import AppError


def list_content_sources(libraries: set[str] | None = None) -> list[dict]:
    selected = [(library, name) for library, name in (("care_manual", "养护手册"), ("cloud_video", "云端视频"))
                if libraries is None or library in libraries]
    # Parallel reads leave the publishing request enough time for the model review.
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(_content_source, library, name) for library, name in selected]
        return [future.result() for future in pending]


def _content_source(library: str, name: str) -> dict:
    source = {"id": library, "name": name, "available": True, "count": 0, "categories": [], "message": ""}
    if library == "cloud_video":
        source["categories"] = [{**category, "count": None} for category in VIDEO_CONTENT_CATEGORIES]
    try:
        if library == "care_manual":
            manuals = list_care_manuals(page_size=1, source_type="manual")
            source["count"] = manuals["stats"]["active"]
        else:
            source["categories"] = list_agent_video_categories()
            source["count"] = sum(category["count"] for category in source["categories"])
        if not source["count"]:
            source["message"] = f"{name}暂无可用内容，请先在资料库中准备内容。"
    except AppError:
        source.update(available=False, count=None, message=f"{name}暂时无法加载，请稍后重试。")
    return source


def content_source_issues(flow: CanvasDefinition) -> list[CanvasIssue]:
    nodes = [node for node in flow.nodes if node.type == "scheduled_send" and node.content_source]
    if not nodes:
        return []
    sources = {source["id"]: source for source in list_content_sources({node.content_source.library for node in nodes})}
    issues = []
    for node in nodes:
        selected = node.content_source
        source = sources[selected.library]
        message = source["message"]
        if source["available"] and source["count"] and selected.library == "cloud_video":
            if not any(category["id"] == selected.category and category["count"] > 0 for category in source["categories"]):
                message = f"云端视频分类「{selected.category}」已不存在或暂无可用视频。"
        if message:
            issues.append(CanvasIssue(node_id=node.id, field="content_source", message=message,
                                      suggestion="刷新内容库后重新选择可用内容；资料库恢复或补充内容后再发布。"))
    return issues
