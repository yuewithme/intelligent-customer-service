import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bootstrap.application import _register_exception_handlers
from app.core.config import get_settings
from app.domains.orchestration.api.admin_workbench import router
from app.domains.orchestration.schemas.canvas_workflow import CanvasDefinition, publication_issues
from app.domains.orchestration.schemas.sop_flow import SopFlowDefinition
from app.domains.orchestration.services import canvas_workflow_service as canvases
from app.domains.orchestration.services.sop_flow_service import build_workbench_packages, save_flow
from app.shared.schemas.common import AppError, ErrorCode


BASE = "/api/v1/admin/orchestration/workbench/canvas/workflows"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'canvas.db'}")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-placeholder")
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
        assert {node.name for node in flow.nodes if node.type == "general"} == set(old_names.values())
        actual_edges = {(names[edge.source], names[edge.target], edge.label) for edge in flow.edges}
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
