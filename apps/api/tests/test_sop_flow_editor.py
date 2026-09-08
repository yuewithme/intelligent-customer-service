from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.bootstrap.application import _register_exception_handlers
from app.domains.orchestration.api.admin_workbench import router
from app.domains.orchestration.schemas.sop_flow import SopFlowDefinition, FlowNode, FlowEdge
from app.domains.orchestration.services import sop_flow_service as flows
from app.domains.orchestration.services.sop_flow_defaults import build_default_sop_packages
from app.domains.sales.services import service_material_touch_service as touches
from app.domains.sales.services.sop_policy_service import eligible_sop_scopes
from app.infrastructure.database.models import AgentWakeupModel
from test_service_material_touch_service import _configure, _insert_contact, _tag_service_customer


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    get_settings.cache_clear()


def default(scope="service"):
    return flows.default_flow(next(item for item in build_default_sop_packages({}) if item["sop_scope"] == scope))


def without(flow, node_id):
    flow.steps = [node for node in flow.steps if node.step_id != node_id]
    flow.transitions = [edge for edge in flow.transitions if node_id not in (edge.from_step, edge.to_step)]
    flow.layout.pop(node_id)
    return SopFlowDefinition.model_validate(flow.model_dump())


def test_api_save_persists_layout_and_rejects_stale_editor():
    app = FastAPI()
    _register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)
    flow = default()
    flow.layout["entry"].x = 123
    response = client.put("/api/v1/admin/orchestration/workbench/flows/service", json=flow.model_dump())
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == 1
    flows._factories.clear()
    package = next(p for p in client.get("/api/v1/admin/orchestration/workbench").json()["data"]["experience_packages"] if p["sop_scope"] == "service")
    assert package["layout"]["entry"]["x"] == 123
    assert package["flow_revision"] == 1
    assert client.put("/api/v1/admin/orchestration/workbench/flows/service", json=flow.model_dump()).status_code == 409
    assert client.put("/api/v1/admin/orchestration/workbench/flows/unknown", json=flow.model_dump()).status_code == 422


@pytest.mark.parametrize("invalid", ["orphan", "cycle", "missing_time", "bad_time", "missing_goal", "duplicate", "timer_after_dialogue"])
def test_invalid_graph_cannot_be_published(invalid):
    data = default().model_dump()
    if invalid == "orphan":
        data["transitions"] = [edge for edge in data["transitions"] if edge["to_step"] != "story"]
    elif invalid == "cycle":
        data["transitions"].append(dict(transition_id="cycle", from_step="daily_touch", to_step="entry", label="循环"))
    elif invalid == "missing_time":
        next(node for node in data["steps"] if node["step_id"] == "story")["schedule"] = None
    elif invalid == "bad_time":
        next(node for node in data["steps"] if node["step_id"] == "story")["schedule"]["time"] = "25:00"
    elif invalid == "missing_goal":
        next(node for node in data["steps"] if node["step_id"] == "need_discovery")["goal"] = ""
    elif invalid == "duplicate":
        data["steps"].append(data["steps"][0])
    else:
        next(edge for edge in data["transitions"] if edge["to_step"] == "story")["from_step"] = "need_discovery"
    with pytest.raises(ValidationError):
        SopFlowDefinition.model_validate(data)


def test_changed_entry_rules_control_customer_eligibility():
    flow = default()
    flow.entry_rule.required_tags = ["春兰"]
    flow.entry_rule.excluded_tags = ["服务中"]
    flows.save_flow("service", flow)
    settings = {"service": True, "first_order": False, "seeding": False}
    assert eligible_sop_scopes(["服务中"], settings) == []
    assert eligible_sop_scopes(["春兰"], settings) == ["service"]


def test_add_and_change_timed_nodes_updates_real_pending_schedule():
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    assert touches.ensure_service_material_touch_tasks(now=now) == 3
    flow = default()
    next(node for node in flow.steps if node.step_id == "story").schedule.time = "08:00"
    extra = FlowNode(step_id="extra", name="晚间分享", type="action", schedule={"time": "18:00", "copy_type": "养护科普"})
    flow.steps.append(extra)
    flow.layout["extra"] = flow.layout["story"].model_copy()
    flow.transitions.append(FlowEdge(transition_id="extra_edge", from_step="daily_touch", to_step="extra", label="18点"))
    flows.save_flow("service", SopFlowDefinition.model_validate(flow.model_dump()))
    assert touches.ensure_service_material_touch_tasks(now=now) == 1
    with touches._database_session() as session:
        rows = {row.reason: row for row in session.query(AgentWakeupModel).all()}
        assert rows["service_material:story"].due_at.hour == 0
        assert rows["service_material:extra"].due_at.hour == 10


@pytest.mark.asyncio
async def test_deleted_timed_node_cannot_process_or_send():
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    touches.ensure_service_material_touch_tasks(now=now)
    flows.save_flow("service", without(default(), "story"))
    assert await touches.process_due_service_material_touches(now=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)) == 0
    with touches._database_session() as session:
        row = session.query(AgentWakeupModel).filter_by(reason="service_material:story").one()
        assert row.status == "cancelled"
        row.status = "queued"
        key = f"service_material_touch:{row.id}"
        session.commit()
    assert not touches.validate_service_material_touch_before_send(key)


def test_edges_drive_progression_and_layout_does_not_reset_cursor():
    flow = default("first_order")
    choices = flows.dialogue_choices(flow, "opening")
    assert "need_discovery" in choices and "pain_discovery" not in choices
    edges = {edge.from_step: edge for edge in flow.transitions}
    edges["opening"].to_step = "pain_discovery"
    edges["pain_discovery"].to_step = "need_discovery"
    edges["need_discovery"].to_step = "recommendation"
    flow = flows.save_flow("first_order", SopFlowDefinition.model_validate(flow.model_dump()))
    assert "pain_discovery" in flows.dialogue_choices(flow, "opening")
    assert "need_discovery" not in flows.dialogue_choices(flow, "opening")
    message = SimpleNamespace(user_id="one", session_id="one", tenant_id="one")
    flows.save_cursor(message, "first_order", flow, "pain_discovery")
    flow.layout["opening"].x += 20
    flow = flows.save_flow("first_order", flow)
    flows._factories.clear()
    assert flows.load_cursor(message, "first_order", flow) == "pain_discovery"


@pytest.mark.asyncio
async def test_custom_dialogue_is_executed_and_removed_node_is_rejected(monkeypatch):
    from test_agent_harness_v2 import _message, _decision
    from app.domains.decisioning.services import agent_runtime
    from app.domains.customers.schemas.state import UserState
    flow = default()
    flow.steps.append(FlowNode(step_id="greeting", name="新增接待", type="agent_stage", goal="简短问候"))
    flow.layout["greeting"] = flow.layout["need_discovery"].model_copy()
    flow.transitions.append(FlowEdge(transition_id="greeting_edge", from_step="entry", to_step="greeting", label="客户打招呼"))
    flow = flows.save_flow("service", SopFlowDefinition.model_validate(flow.model_dump()))
    result = _decision(sop_node="service.greeting", final={"messages": [{"type": "text", "content": "您好，我在的。"}], "need_human": False})
    async def generate(*args, **kwargs):
        return result
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    message = _message("你好")
    message.metadata.update(sop_scope="service", sop_scopes=["service"])
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id="customer-1"), workspace={})
    assert reply.metadata["agent_runtime"]["sop_node"] == "service.greeting"
    assert reply.answer == "您好，我在的。"
    assert flows.load_cursor(message, "service", flow) == "greeting"
    flows.save_flow("service", without(flow, "greeting"))
    repaired = _decision(sop_node="general.reply", final={"messages": [{"type": "text", "content": "您好，我在的。"}], "need_human": False})
    decisions = iter([result, repaired])
    async def generate_repair(*args, **kwargs):
        return next(decisions)
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate_repair)
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id="customer-1"), workspace={})
    assert reply.metadata["agent_runtime"]["sop_node"] == "general.reply"
    assert reply.metadata["agent_runtime"]["attempt_trace"][0]["outcome"] == "invalid_schema"


@pytest.mark.asyncio
async def test_saved_opening_obeys_entry_and_uses_configured_dialogue(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service
    from app.domains.customers.services import user_profile_service
    flow = default("first_order")
    flow.entry_rule.required_tags = ["新客"]
    flow = flows.save_flow("first_order", flow)
    tags, calls, recorded, queued = [], [], [], []
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    async def profile(*args, **kwargs):
        return {"profile": {"customer_tags": tags}}
    async def chat(request):
        calls.append(request)
        return {"answer": "欢迎，您想先了解哪个品种？", "need_human": False}
    async def memories(*args, **kwargs):
        pass
    async def message(**kwargs):
        recorded.append(kwargs)
        return {"id": len(recorded)}
    async def enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}
    async def slots(**kwargs):
        return [now] * kwargs["message_count"]
    monkeypatch.setattr(user_profile_service, "get_profile_bundle", profile)
    monkeypatch.setattr(service, "handle_chat", chat)
    monkeypatch.setattr(service, "_record_opening_memories", memories)
    monkeypatch.setattr(service, "ensure_outbound_conversation_message", message)
    monkeypatch.setattr(service, "enqueue_eyun_outbound", enqueue)
    monkeypatch.setattr(service, "_reserve_opening_delivery_slots", slots)
    batch = {"w_id": "wid", "target_wc_id": "customer", "from_user": "customer", "from_group": None,
             "batch_key": "wid:customer", "created_at": now}
    await service._send_opening_for_new_friend(batch)
    assert calls == queued == []
    tags.append("新客")
    await service._send_opening_for_new_friend(batch)
    assert calls[0].metadata["system_event"] == "workflow_entry"
    assert queued[0]["content"] == "欢迎，您想先了解哪个品种？"
    assert recorded[0]["metadata"]["flow_version"] == flows.execution_version(flow)
