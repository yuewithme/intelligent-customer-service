import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bootstrap.application import _register_exception_handlers
from app.core.config import get_settings
from app.domains.orchestration.api.admin_workbench import router
from app.domains.orchestration.schemas.canvas_workflow import CanvasDefinition, publication_issues
from app.domains.orchestration.schemas.sop_flow import SopFlowDefinition
from app.domains.orchestration.services import canvas_workflow_service as canvases
from app.domains.orchestration.services import canvas_content_service as content
from app.domains.orchestration.services.sop_flow_service import build_workbench_packages, save_flow
from app.shared.schemas.common import AppError, ErrorCode


BASE = "/api/v1/admin/orchestration/workbench/canvas/workflows"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'canvas.db'}")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(content, "list_activities", lambda **kwargs: {"items": [], "total": 0})
    get_settings.cache_clear()
    app = FastAPI()
    _register_exception_handlers(app)
    app.include_router(router)
    with TestClient(app) as client:
        yield client
    for factory in canvases._factories.values():
        factory.kw["bind"].dispose()
    canvases._factories.clear()
    get_settings.cache_clear()


def create(client, *, complete=True):
    created = client.post(BASE, json={"name": "新好友养护服务"}).json()["data"]
    if not complete:
        return created
    flow = created["definition"]
    flow["nodes"][0]["trigger_condition"] = "客户加上好友时触发"
    flow["nodes"][1].update(trigger_condition="客户提出养护问题", action="查阅养护手册，回答客户问题", exit_condition="客户确认已解决后结束")
    return client.put(f"{BASE}/{created['workflow_id']}", json={"revision": created["revision"], "definition": flow}).json()["data"]


def accepted_review():
    return {"summary": "新好友提出养护问题后提供帮助，确认解决后结束。", "nodes": [
        {"node_id": key, "understanding": description}
        for key, description in [("start", "加好友事件触发"), ("step", "根据养护资料回答问题，确认解决后退出"), ("end", "结束本次流程")]
    ], "issues": []}


def test_incomplete_draft_round_trip_and_conflict(client):
    created = create(client, complete=False)
    url = f"{BASE}/{created['workflow_id']}"
    flow = created["definition"]
    flow["nodes"] = []
    flow["edges"] = []
    saved = client.put(url, json={"revision": 1, "definition": flow})
    assert saved.status_code == 200
    assert client.get(url).json()["data"]["definition"] == flow
    assert client.put(url, json={"revision": 1, "definition": flow}).status_code == 409
    assert client.get(BASE).json()["data"][0]["workflow_id"] == created["workflow_id"]


def test_content_sources_use_ready_video_categories_and_manual_availability(client, monkeypatch):
    from app.domains.catalog.services import agent_media_library_service as media

    def manuals(**kwargs):
        assert kwargs["page_size"] == 200 and kwargs["enabled"] is True
        if kwargs["source_type"] == "course":
            return {"items": [], "total": 0}
        return {"items": [{"id": i, "title": f"手册 {i}", "source_type": "manual", "auto_send_allowed": True} for i in range(3)], "total": 3}

    monkeypatch.setattr(content, "list_care_manuals", manuals)
    monkeypatch.setattr(media, "_load_items", lambda: [
        {"id": str(index), "title": f"素材 {index}", "media_type": media_type, "copy_type": category, "copy_status": status}
        for index, (media_type, category, status) in enumerate([
            ("video", "养护科普", "ready"), ("video", "养护科普", "ready"),
            ("video", "未知分类", "ready"), ("video", "名品故事", "pending"),
            ("image", "图片内容", "ready"), ("video", "", "ready"),
        ])
    ])
    sources = client.get("/api/v1/admin/orchestration/workbench/canvas/content-sources").json()["data"]
    assert sources[0]["id"] == "care_manual" and sources[0]["count"] == 3
    assert sources[1]["count"] == 2 and sources[1]["available"] is True
    assert [item["id"] for item in sources[1]["categories"]] == ["养护科普", "话题种草", "名品故事"]
    assert {item["id"]: item["count"] for item in sources[1]["categories"]} == {"养护科普": 2, "话题种草": 0, "名品故事": 0}
    assert all(item["description"] for item in sources[1]["categories"])

    def unavailable():
        raise AppError(ErrorCode.INTERNAL_ERROR, "不可用", status_code=503)

    monkeypatch.setattr(media, "_load_items", unavailable)
    partial = client.get("/api/v1/admin/orchestration/workbench/canvas/content-sources").json()["data"]
    assert partial[0]["available"] is True and partial[0]["count"] == 3
    assert partial[1]["available"] is False and partial[1]["count"] is None
    assert "无法加载" in partial[1]["message"]
    assert [item["id"] for item in partial[1]["categories"]] == ["养护科普", "话题种草", "名品故事"]
    assert all(item["count"] is None for item in partial[1]["categories"])
    monkeypatch.setattr(media, "_load_items", lambda: [])
    empty = client.get("/api/v1/admin/orchestration/workbench/canvas/content-sources").json()["data"][1]
    assert empty["available"] is True and empty["count"] == 0 and "暂无" in empty["message"]
    assert len(empty["categories"]) == 3 and all(item["count"] == 0 for item in empty["categories"])


def test_manual_source_publishes_without_copy_and_invalid_sources_do_not_reach_ai(client, monkeypatch):
    created = create(client)
    flow = created["definition"]
    timer = flow["nodes"][1]
    timer.update(type="scheduled_send", send_time="10:00", action="", content_source={"library": "care_manual", "category": ""})
    monkeypatch.setattr(content, "list_care_manuals", lambda **kwargs: {"items": [{"id": 1, "title": "手册", "source_type": "manual", "auto_send_allowed": True}], "total": 1})

    async def review(prompt, **kwargs):
        payload = json.loads(prompt.split("下面是完整的待审核流程 JSON：\n")[1])
        assert payload["nodes"][1]["content_source"] == {"library": "care_manual", "category": "", "selection": "auto", "item_ids": []}
        return accepted_review()

    monkeypatch.setattr(canvases, "generate_json", review)
    url = f"{BASE}/{created['workflow_id']}"
    saved = client.put(url, json={"revision": 2, "definition": flow}).json()["data"]
    assert client.get(url).json()["data"]["definition"] == saved["definition"]
    result = client.post(f"{url}/publish", json={"revision": 3}).json()["data"]
    assert result["published"] is True
    snapshot = result["workflow"]["published"]

    async def unexpected(*args, **kwargs):
        pytest.fail("Invalid content sources must be checked before calling AI")

    monkeypatch.setattr(canvases, "generate_json", unexpected)
    monkeypatch.setattr(content, "list_agent_content", lambda media_type: {"categories": [], "items": [
        {"id": "care-video", "name": "养护视频", "category": "养护科普", "description": ""},
    ]})
    for revision, source in enumerate([None, {"library": "cloud_video", "category": ""}, {"library": "cloud_video", "category": "已删除分类"}, {"library": "cloud_video", "category": "名品故事"}], 3):
        timer["content_source"] = source
        assert client.put(url, json={"revision": revision, "definition": flow}).status_code == 200
        failed = client.post(f"{url}/publish", json={"revision": revision + 1}).json()["data"]
        assert failed["published"] is False
        assert failed["review"]["issues"][0]["field"] == "content_source"
        assert failed["workflow"]["published"] == snapshot

    def unavailable(**kwargs):
        raise AppError(ErrorCode.INTERNAL_ERROR, "不可用", status_code=503)

    monkeypatch.setattr(content, "list_care_manuals", unavailable)
    timer["content_source"] = {"library": "care_manual", "category": ""}
    assert client.put(url, json={"revision": 7, "definition": flow}).status_code == 200
    failed = client.post(f"{url}/publish", json={"revision": 8}).json()["data"]
    assert failed["published"] is False and "无法加载" in failed["review"]["issues"][0]["message"]


def test_other_content_libraries_filter_unavailable_rows_and_include_later_pages(client, monkeypatch):
    from app.domains.catalog.services import agent_media_library_service as media

    def manuals(**kwargs):
        if kwargs["source_type"] == "manual":
            rows = [{"id": index, "source_type": "manual", "title": f"养护手册 {index}", "auto_send_allowed": True} for index in range(201)]
            rows.append({"id": 900, "source_type": "manual", "title": "已停用手册", "auto_send_allowed": False})
        else:
            rows = [{"id": 301, "source_type": "course", "title": "免费入门课程", "auto_send_allowed": True, "price_cent": 0},
                    {"id": 302, "source_type": "course", "title": "付费课程", "auto_send_allowed": True, "price_cent": 100},
                    {"id": 303, "source_type": "course", "title": "未发布课程", "auto_send_allowed": False, "price_cent": 0}]
        offset = (kwargs["page"] - 1) * kwargs["page_size"]
        return {"items": rows[offset:offset + kwargs["page_size"]], "total": len(rows)}

    monkeypatch.setattr(content, "list_care_manuals", manuals)
    monkeypatch.setattr(content, "list_activities", lambda **kwargs: {"total": 3, "items": [
        {"id": 401, "title": "会员日活动", "enabled": True, "ai_enabled": True, "effective_status": "active", "item_count": 2},
        {"id": 402, "title": "仅人工活动", "enabled": True, "ai_enabled": False, "effective_status": "active", "item_count": 2},
        {"id": 403, "title": "已过期活动", "enabled": True, "ai_enabled": True, "effective_status": "expired", "item_count": 2},
    ]})
    monkeypatch.setattr(media, "_load_items", lambda: [
        {"id": "care-image", "title": "养护图文", "media_type": "image", "copy_type": "养护科普", "copy_status": "ready"},
        {"id": "care-video", "title": "养护视频", "media_type": "video", "copy_type": "养护科普", "copy_status": "ready"},
    ])
    sources = {item["id"]: item for item in client.get("/api/v1/admin/orchestration/workbench/canvas/content-sources").json()["data"]}
    assert set(sources) == {"care_manual", "cloud_video", "cloud_image", "care_course", "sales_activity"}
    assert sources["care_manual"]["count"] == 201 and sources["care_manual"]["items"][-1]["id"] == "200"
    assert [item["id"] for item in sources["care_course"]["items"]] == ["301"]
    assert [item["id"] for item in sources["sales_activity"]["items"]] == ["401"]
    assert [item["id"] for item in sources["cloud_image"]["items"]] == ["care-image"]
    assert [item["id"] for item in sources["cloud_video"]["items"]] == ["care-video"]


@pytest.mark.parametrize("library,category", [("care_manual", ""), ("cloud_video", "养护科普"), ("cloud_image", "养护科普"), ("care_course", ""), ("sales_activity", "")])
def test_specific_content_references_round_trip_and_resolve_titles_for_review(client, monkeypatch, library, category):
    created = create(client)
    flow = created["definition"]
    timer = flow["nodes"][1]
    timer.update(type="scheduled_send", send_time="10:00", action="", content_source={
        "library": library, "category": category, "selection": "specific", "item_ids": ["item-1"],
    })
    available = {"id": library, "name": "选择的内容库", "description": "内容匹配规则", "message": "", "available": True, "count": 1,
                 "items": [{"id": "item-1", "name": "建兰夏季养护内容", "category": category, "description": "适合建兰客户"}]}
    monkeypatch.setattr(content, "list_content_sources", lambda libraries: [available])

    async def review(prompt, **kwargs):
        payload = json.loads(prompt.split("下面是完整的待审核流程 JSON：\n")[1])
        assert payload["nodes"][1]["content_source"] == timer["content_source"]
        assert payload["selected_contents"]["step"]["items"][0]["name"] == "建兰夏季养护内容"
        return accepted_review()

    monkeypatch.setattr(canvases, "generate_json", review)
    url = f"{BASE}/{created['workflow_id']}"
    saved = client.put(url, json={"revision": 2, "definition": flow}).json()["data"]
    assert client.get(url).json()["data"]["definition"] == saved["definition"]
    published = client.post(f"{url}/publish", json={"revision": 3}).json()["data"]
    assert published["published"] is True

    async def unexpected(*args, **kwargs):
        pytest.fail("Invalid item references must not reach AI")

    monkeypatch.setattr(canvases, "generate_json", unexpected)
    for revision, item_ids in enumerate([[], ["removed-item"]], 3):
        timer["content_source"]["item_ids"] = item_ids
        assert client.put(url, json={"revision": revision, "definition": flow}).status_code == 200
        failed = client.post(f"{url}/publish", json={"revision": revision + 1}).json()["data"]
        assert failed["published"] is False and failed["review"]["issues"][0]["field"] == "content_source"
        assert failed["workflow"]["published"] == published["workflow"]["published"]
    if category:
        timer["content_source"].update(category="名品故事", item_ids=["item-1"])
        assert client.put(url, json={"revision": 5, "definition": flow}).status_code == 200
        failed = client.post(f"{url}/publish", json={"revision": 6}).json()["data"]
        assert failed["published"] is False and failed["review"]["issues"][0]["field"] == "content_source"


def test_sync_all_three_sops_preserves_nodes_branches_and_policies(client):
    response = client.post("/api/v1/admin/orchestration/workbench/canvas/sync-sops")
    assert response.status_code == 200
    assert response.json()["data"]["created"] == ["sop_first_order", "sop_service", "sop_seeding"]
    packages = build_workbench_packages({}, {"first_order": True, "service": True, "seeding": False})
    assert {item["name"] for item in client.get(BASE).json()["data"]} == {"首单 SOP", "服务 SOP", "种草 SOP"}
    for package in packages:
        detail = client.get(f"{BASE}/sop_{package['sop_scope']}").json()["data"]
        flow = CanvasDefinition.model_validate(detail["definition"])
        assert not publication_issues(flow)
        assert detail["revision"] == 1 and detail["published"] is None
        names = {node.id: node.name for node in flow.nodes}
        old_names = {node["step_id"]: node["name"] for node in package["steps"]}
        assert {node.name for node in flow.nodes if node.type not in {"start", "end"}} == set(old_names.values())
        assert {node.type for node in flow.nodes} <= {"start", "general", "scheduled_send", "condition", "handoff", "end"}
        branch_conditions = {(node.id, branch.id): branch.condition for node in flow.nodes for branch in node.branches}
        actual_edges = {(names[edge.source], names[edge.target], branch_conditions.get((edge.source, edge.source_branch), edge.label)) for edge in flow.edges}
        for edge in package["transitions"]:
            assert (old_names[edge["from_step"]], old_names.get(edge["to_step"], "结束本次分支"), edge["label"]) in actual_edges
        for node in flow.nodes:
            for other in flow.nodes:
                if node.id != other.id:
                    assert abs(node.position.x - other.position.x) >= 260 or abs(node.position.y - other.position.y) >= 252
        assert client.put(f"{BASE}/sop_{package['sop_scope']}", json={"revision": 1, "definition": flow.model_dump()}).status_code == 200

    service = client.get(f"{BASE}/sop_service").json()["data"]["definition"]
    service_text = "\n".join(node["trigger_condition"] + node["action"] for node in service["nodes"])
    assert all(text in service_text for text in ["每天 10:00", "每天 14:00", "每周六 11:00", "每周日 14:00", "不直接查商品", "不发送商品卡片", "进入本节点即转人工", "每轮最多问一项"])
    seeding = client.get(f"{BASE}/sop_seeding").json()["data"]["definition"]
    assert "每天 15:00" in seeding["nodes"][0]["trigger_condition"]
    assert any("普通确认、感谢或仅有偏好标签" in node["trigger_condition"] for node in seeding["nodes"])
    assert sum(node["type"] == "scheduled_send" for node in service["nodes"]) == 4
    assert sum(node["type"] == "handoff" for node in service["nodes"]) == 1
    assert next(node for node in seeding["nodes"] if node["type"] == "scheduled_send")["send_content_type"] == "mixed"


def test_sync_prefers_saved_flow_and_never_overwrites_edited_or_published_canvas(client):
    saved = SopFlowDefinition.model_validate({
        "start_step_id": "start", "entry_rule": {"any_tags": ["自定义入口"], "required_tags": ["已验证"], "excluded_tags": ["已退出"]},
        "steps": [
            {"step_id": "start", "name": "自定义判断", "type": "decision", "goal": "按事件选择分支"},
            {"step_id": "end", "name": "自定义人工服务", "type": "agent_stage", "goal": "处理客户定制需求", "directions": ["先核对预约记录"], "handoff_enabled": True},
            {"step_id": "appointment", "name": "预约提醒", "type": "action", "goal": "发送约定的养护内容", "schedule": {"time": "09:25", "weekday": 2, "copy_type": "养护科普"}},
        ],
        "transitions": [
            {"transition_id": "one", "from_step": "start", "to_step": "end", "label": "收到定制请求", "priority": 10},
            {"transition_id": "two", "from_step": "start", "to_step": "appointment", "label": "约定时间到达", "priority": 20},
        ],
        "layout": {key: {"x": 0, "y": 0} for key in ["start", "end", "appointment"]},
    })
    save_flow("first_order", saved)
    assert canvases.sync_existing_sops()["created"]
    imported = canvases.get_canvas("sop_first_order")
    definition = CanvasDefinition.model_validate(imported["definition"])
    assert not publication_issues(definition)
    assert "自定义入口" in definition.nodes[0].trigger_condition and "每周三 09:25" in definition.nodes[0].trigger_condition
    custom = next(node for node in definition.nodes if node.name == "自定义人工服务")
    assert "先核对预约记录" in custom.action and "即转人工" in custom.action
    definition.name = "团队修改后的流程"
    custom.action = "由团队自定义行动"
    canvases.save_canvas("sop_first_order", 1, definition)
    with canvases._session() as session:
        row = session.get(canvases.CanvasWorkflowModel, "sop_first_order")
        row.published_revision = 2
        row.published_json = '{"marker":"existing published snapshot"}'
        session.commit()
    before = canvases.get_canvas("sop_first_order")
    result = client.post("/api/v1/admin/orchestration/workbench/canvas/sync-sops").json()["data"]
    assert result["created"] == []
    assert canvases.get_canvas("sop_first_order") == before
    assert len(canvases.list_canvases()) == 3


@pytest.mark.parametrize("cycle", ["daily", "every_three_days", "weekly"])
def test_specialized_nodes_round_trip_validate_and_publish_full_configuration(client, monkeypatch, cycle):
    created = create(client)
    flow = created["definition"]
    condition = flow["nodes"][1]
    condition.update(type="condition", name="判断客户需求", trigger_condition="", action="", exit_condition="",
                     branches=[{"id": "care", "condition": "客户有服务中标签且到了发送时间"}, {"id": "human", "condition": "客户要求人工"}])
    timed = {**condition, "id": "timed", "type": "scheduled_send", "name": "养护内容", "branches": [],
             "send_time": "09:25", "send_cycle": cycle, "send_content_type": "mixed", "action": "",
             "content_source": {"library": "cloud_video", "category": "养护科普"}}
    monkeypatch.setattr(content, "list_agent_content", lambda media_type: {"categories": [], "items": [
        {"id": "care-video", "name": "养护视频", "category": "养护科普", "description": ""},
    ]})
    handoff = {**condition, "id": "handoff", "type": "handoff", "name": "人工接待", "branches": [], "handoff_target": ""}
    flow["nodes"].extend([timed, handoff])
    flow["edges"] = [flow["edges"][0],
        {"id": "to_timed", "source": "step", "target": "timed", "source_branch": "care", "label": ""},
        {"id": "to_handoff", "source": "step", "target": "handoff", "source_branch": "human", "label": ""},
        {"id": "timed_end", "source": "timed", "target": "end", "label": "本次发送完成"},
        {"id": "handoff_end", "source": "handoff", "target": "end", "label": "交接完成"},
    ]
    url = f"{BASE}/{created['workflow_id']}"
    saved = client.put(url, json={"revision": 2, "definition": flow}).json()["data"]
    assert client.get(url).json()["data"]["definition"] == saved["definition"]

    async def review(prompt, **kwargs):
        payload = json.loads(prompt.split("下面是完整的待审核流程 JSON：\n")[1])
        reviewed = {node["id"]: node for node in payload["nodes"]}
        assert reviewed["step"]["branches"] == condition["branches"]
        assert "action" not in reviewed["step"] and "handoff_target" not in reviewed["handoff"]
        assert reviewed["timed"]["send_time"] == "09:25" and reviewed["timed"]["send_cycle"] == cycle
        assert reviewed["timed"]["content_source"] == {**timed["content_source"], "selection": "auto", "item_ids": []}
        assert "send_content_type" not in reviewed["timed"] and reviewed["timed"]["action"] == ""
        assert "现有转人工设置通知负责人" in prompt and "底层打标能力" in prompt
        assert kwargs["prompt_version"] == "canvas_publish.v6"
        return {"summary": "按条件执行定时发送或转人工", "nodes": [{"node_id": node["id"], "understanding": node["name"]} for node in flow["nodes"]], "issues": []}
    monkeypatch.setattr(canvases, "generate_json", review)
    result = client.post(f"{url}/publish", json={"revision": 3}).json()["data"]
    assert result["published"] is True
    assert result["workflow"]["published"]["definition"] == saved["definition"]

    assert saved["definition"]["edges"][1]["label"] == "分支一"
    assert saved["definition"]["edges"][2]["label"] == "分支二"
    timed["send_time"] = ""
    condition["branches"][0]["condition"] = ""
    issues = publication_issues(CanvasDefinition.model_validate(flow))
    assert {(issue.node_id, issue.field) for issue in issues} == {("timed", "send_time"), ("step", "branches")}
    flow["edges"].pop(2)
    assert any("分支二还没有连接" in issue.message for issue in publication_issues(CanvasDefinition.model_validate(flow)))


def test_upgrade_only_untouched_legacy_imports(client):
    from app.domains.orchestration.services.sop_canvas_import import sop_to_canvas
    from app.domains.orchestration.services.sop_flow_defaults import build_default_sop_packages
    from app.domains.orchestration.services.sop_flow_service import default_flow
    from app.domains.sales.services.tag_catalog import get_tag_categories

    categories = {key: value.name for key, value in get_tag_categories().items()}
    old_fields = {"schedule_rule", "send_content_type", "branch_mode", "handoff_target"}
    with canvases._session() as session:
        for package in build_default_sop_packages({}):
            flow = sop_to_canvas(package["sop_scope"], package["name"], default_flow(package), categories, specialized=False)
            legacy = flow.model_dump(exclude={"nodes": {"__all__": old_fields}})
            row = canvases.CanvasWorkflowModel(workflow_id=f"sop_{package['sop_scope']}", revision=1,
                                              draft_json=json.dumps(legacy, ensure_ascii=False), updated_at=datetime.now(timezone.utc))
            if package["sop_scope"] == "first_order":
                row.revision = 2
                legacy["nodes"][1]["action"] = "用户已修改的行动"
                row.draft_json = json.dumps(legacy, ensure_ascii=False)
            if package["sop_scope"] == "seeding":
                row.published_revision = 1
                row.published_json = '{"summary":"published"}'
            session.add(row)
        session.commit()
    first, seeding = canvases.get_canvas("sop_first_order"), canvases.get_canvas("sop_seeding")
    service_before = canvases.get_canvas("sop_service")
    result = canvases.sync_existing_sops()
    assert result == {"created": [], "updated": ["sop_service"]}
    service = canvases.get_canvas("sop_service")
    assert service["revision"] == 2
    assert {node["type"] for node in service["definition"]["nodes"]} == {"start", "condition", "scheduled_send", "handoff", "general", "end"}
    assert [node["position"] for node in service["definition"]["nodes"]] == [node["position"] for node in service_before["definition"]["nodes"]]
    assert {(edge["id"], edge["source"], edge["target"]) for edge in service["definition"]["edges"]} == {(edge["id"], edge["source"], edge["target"]) for edge in service_before["definition"]["edges"]}
    assert canvases.get_canvas("sop_first_order") == first and canvases.get_canvas("sop_seeding") == seeding
    assert canvases.sync_existing_sops() == {"created": [], "updated": []}


def test_branch_identity_survives_insert_delete_and_shared_destination(client):
    created = create(client)
    flow = created["definition"]
    branch_node = flow["nodes"][1]
    branch_node.update(type="condition", trigger_condition="", action="", exit_condition="", branches=[
        {"id": "a", "condition": "客户已完成首单"}, {"id": "b", "condition": "其他情况"},
    ])
    flow["edges"] = [flow["edges"][0],
        {"id": "a_end", "source": "step", "source_branch": "a", "target": "end", "label": "分支一"},
        {"id": "b_end", "source": "step", "source_branch": "b", "target": "end", "label": "分支二"},
    ]
    url = f"{BASE}/{created['workflow_id']}"
    result = client.put(url, json={"revision": 2, "definition": flow})
    assert result.status_code == 200
    flow = result.json()["data"]["definition"]
    assert not publication_issues(CanvasDefinition.model_validate(flow))
    flow["nodes"][1]["branches"].insert(0, {"id": "new", "condition": "客户被标记为测试用户"})
    flow["edges"].append({"id": "new_end", "source": "step", "source_branch": "new", "target": "end", "label": "任意旧显示名"})
    saved = client.put(url, json={"revision": 3, "definition": flow}).json()["data"]
    assert {edge["source_branch"]: edge["label"] for edge in saved["definition"]["edges"] if edge["source_branch"]} == {"new": "分支一", "a": "分支二", "b": "分支三"}
    flow = saved["definition"]
    flow["nodes"][1]["branches"] = [branch for branch in flow["nodes"][1]["branches"] if branch["id"] != "a"]
    assert client.put(url, json={"revision": 4, "definition": flow}).status_code == 422
    flow["edges"] = [edge for edge in flow["edges"] if edge["source_branch"] != "a"]
    result = client.put(url, json={"revision": 4, "definition": flow})
    assert result.status_code == 200
    loaded = client.get(url).json()["data"]["definition"]
    assert next(edge for edge in loaded["edges"] if edge["source_branch"] == "b")["label"] == "分支二"
    assert not publication_issues(CanvasDefinition.model_validate(loaded))


def test_legacy_conditions_and_weekday_are_exposed_without_modifying_snapshot(client):
    legacy = {
        "name": "旧每周回访", "nodes": [
            {"id": "start", "type": "start", "name": "开始", "position": {"x": 0, "y": 0}, "trigger_condition": "服务客户触发"},
            {"id": "choose", "type": "condition", "name": "区分需求", "position": {"x": 350, "y": 0}},
            {"id": "send", "type": "scheduled_send", "name": "周六回访", "position": {"x": 700, "y": 0}, "schedule_rule": "北京时间每周六 11:00", "action": "发送季节养护提醒\n使用「养护科普」类型的素材。"},
            {"id": "end", "type": "end", "name": "结束", "position": {"x": 1050, "y": 0}},
        ], "edges": [
            {"id": "entry", "source": "start", "target": "choose", "label": "开始"},
            {"id": "care", "source": "choose", "target": "send", "label": "客户需要季节养护服务"},
            {"id": "other", "source": "choose", "target": "end", "label": "其他情况"},
            {"id": "sent", "source": "send", "target": "end", "label": "发送完成"},
        ],
    }
    snapshot = json.dumps({"definition": legacy, "revision": 7}, ensure_ascii=False)
    with canvases._session() as session:
        session.add(canvases.CanvasWorkflowModel(workflow_id="legacy", revision=7, draft_json=json.dumps(legacy, ensure_ascii=False),
                                                published_json=snapshot, published_revision=7, updated_at=datetime.now(timezone.utc)))
        session.commit()
    loaded = client.get(f"{BASE}/legacy").json()["data"]
    condition, timer = loaded["definition"]["nodes"][1:3]
    assert [branch["condition"] for branch in condition["branches"]] == ["客户需要季节养护服务", "其他情况"]
    assert (timer["send_time"], timer["send_cycle"], timer["send_weekday"]) == ("11:00", "weekly", 5)
    assert timer["content_source"] == {"library": "cloud_video", "category": "养护科普", "selection": "auto", "item_ids": []}
    assert timer["action"] == "发送季节养护提醒"
    assert loaded["definition"]["edges"][1]["source_branch"] == condition["branches"][0]["id"]
    assert loaded["published"] == json.loads(snapshot) and loaded["revision"] == 7
    assert client.get(f"{BASE}/legacy").json()["data"] == loaded
    assert not publication_issues(CanvasDefinition.model_validate(loaded["definition"]))
    legacy["nodes"][2]["schedule_rule"] = "每天 09:00 和 18:00"
    ambiguous = CanvasDefinition.model_validate(legacy)
    assert ambiguous.nodes[2].send_time == ""
    assert any(issue.node_id == "send" and issue.field == "send_time" for issue in publication_issues(ambiguous))


def test_structural_errors_locate_fields_without_model_call(client, monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("An incomplete graph must not be sent to the model")
    monkeypatch.setattr(canvases, "generate_json", unexpected)
    created = create(client, complete=False)
    result = client.post(f"{BASE}/{created['workflow_id']}/publish", json={"revision": 1}).json()["data"]
    assert result["published"] is False
    assert {(issue["node_id"], issue["field"]) for issue in result["review"]["issues"]} == {
        ("start", "trigger_condition"), ("step", "trigger_condition"), ("step", "action"), ("step", "exit_condition"),
    }
    assert result["workflow"]["published"] is None


def test_success_uses_qwen_whole_graph_and_failed_review_keeps_publication(client, monkeypatch):
    async def review(prompt, **kwargs):
        assert kwargs["model_override"] == "qwen3.8-max"
        assert kwargs["provider_override"] == "dashscope"
        assert "客户加上好友时触发" in prompt and "查阅养护手册" in prompt and '"edges"' in prompt
        return accepted_review()
    monkeypatch.setattr(canvases, "generate_json", review)
    created = create(client)
    url = f"{BASE}/{created['workflow_id']}"
    published = client.post(f"{url}/publish", json={"revision": 2}).json()["data"]
    assert published["published"] is True
    snapshot = published["workflow"]["published"]
    assert snapshot["definition"] == created["definition"]
    changed = created["definition"]
    changed["nodes"][1]["action"] = "由用户决定是否查商品和发卡"
    client.put(url, json={"revision": 2, "definition": changed})

    async def rejected(*args, **kwargs):
        return {"summary": "发卡条件不明确", "nodes": [], "issues": [{"node_id": "step", "field": "action", "message": "未说明何时发卡", "suggestion": "请写清客户满足什么条件时发卡"}]}
    monkeypatch.setattr(canvases, "generate_json", rejected)
    result = client.post(f"{url}/publish", json={"revision": 3}).json()["data"]
    assert result["published"] is False
    assert result["workflow"]["published"] == snapshot
    assert client.get(url).json()["data"]["last_review"]["issues"][0]["node_id"] == "step"


def test_edit_during_model_review_cannot_publish_stale_draft(client, monkeypatch):
    created = create(client)
    async def edited(*args, **kwargs):
        flow = CanvasDefinition.model_validate(created["definition"])
        flow.name = "审核期间的更新"
        canvases.save_canvas(created["workflow_id"], 2, flow)
        return accepted_review()
    monkeypatch.setattr(canvases, "generate_json", edited)
    url = f"{BASE}/{created['workflow_id']}"
    assert client.post(f"{url}/publish", json={"revision": 2}).status_code == 409
    current = client.get(url).json()["data"]
    assert current["definition"]["name"] == "审核期间的更新"
    assert current["published"] is None


@pytest.mark.parametrize("failure", ["missing_nodes", "unavailable"])
def test_incomplete_or_unavailable_model_does_not_publish(client, monkeypatch, failure):
    async def failed(*args, **kwargs):
        if failure == "unavailable":
            raise AppError(ErrorCode.LLM_FAILED)
        return {"summary": "看起来可以", "nodes": [], "issues": []}
    monkeypatch.setattr(canvases, "generate_json", failed)
    created = create(client)
    url = f"{BASE}/{created['workflow_id']}"
    assert client.post(f"{url}/publish", json={"revision": 2}).status_code == 502
    assert client.get(url).json()["data"]["published"] is None
