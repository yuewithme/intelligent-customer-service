import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.domains.orchestration.schemas.canvas_workflow import CanvasBranch, CanvasDefinition
from app.domains.orchestration.services import canvas_workflow_service as canvases
from app.domains.orchestration.services import sop_flow_service as flows
from app.domains.orchestration.services import canvas_scheduled_content_service as content
from app.domains.orchestration.services.canvas_execution_service import compile_canvas, execution_status
from app.domains.sales.services import service_material_touch_service as touches
from app.domains.sales.services.sop_policy_service import eligible_sop_scopes
from app.infrastructure.database.models import CanvasWorkflowModel, AgentWakeupModel
from test_service_material_touch_service import _configure, _insert_contact, _tag_service_customer


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-placeholder")
    get_settings.cache_clear()
    yield
    for factories in (canvases._factories, flows._factories):
        for factory in factories.values():
            factory.kw["bind"].dispose()
        factories.clear()


def definition(*, cycle="daily"):
    common = {"position": {"x": 0, "y": 0}}
    return CanvasDefinition.model_validate({"name": "服务流程", "nodes": [
        {**common, "id": "start", "type": "start", "name": "开始",
         "trigger_condition": "入口条件（以下各项须同时满足）：\n至少带有以下一个标签：服务中"},
        {**common, "id": "choose", "type": "condition", "name": "按需分流", "branches": [
            {"id": "dialogue", "condition": "客户发来养护问题"},
            {"id": "timer", "condition": "到了定时分享时间"}]},
        {**common, "id": "care", "type": "general", "name": "养护答疑", "trigger_condition": "客户提出养护问题",
         "action": "先回答当前问题，不推荐商品，不重复追问已知信息", "exit_condition": "客户确认问题已解决"},
        {**common, "id": "send", "type": "scheduled_send", "name": "科普分享", "send_time": "10:00",
         "send_cycle": cycle, "content_source": {"library": "cloud_image", "category": "养护科普", "selection": "specific", "item_ids": ["image-1"]}},
        {**common, "id": "end", "type": "end", "name": "结束"}], "edges": [
            {"id": "entry", "source": "start", "target": "choose"},
            {"id": "care_path", "source": "choose", "source_branch": "dialogue", "target": "care"},
            {"id": "timer_path", "source": "choose", "source_branch": "timer", "target": "send"},
            {"id": "care_end", "source": "care", "target": "end"},
            {"id": "send_end", "source": "send", "target": "end"}]})


def install(canvas=None, *, workflow_id="sop_service", revision=2):
    canvas = canvas or definition()
    snapshot = {"definition": canvas.model_dump(), "review": {"issues": []}, "model": "test",
                "revision": revision, "published_at": "2026-11-04T00:30:00+00:00"}
    with canvases._session() as session:
        session.merge(CanvasWorkflowModel(workflow_id=workflow_id, revision=revision,
            draft_json=canvas.model_dump_json(), published_json=json.dumps(snapshot), published_revision=revision,
            published_at=datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc), updated_at=datetime.now(timezone.utc)))
        session.commit()
    return snapshot


def test_published_canvas_is_the_execution_source_but_draft_is_not():
    install()
    live = flows.get_saved_flow("service")
    assert live.workflow_id == "sop_service"
    assert flows.dialogue_choices(live, None) == {"care"}
    assert next(edge for edge in live.transitions if edge.transition_id == "timer_path").label == "到了定时分享时间"
    assert "先回答当前问题" in flows.execution_prompt("service", live, None)
    draft = canvases.get_canvas("sop_service")
    changed = CanvasDefinition.model_validate(draft["definition"])
    next(node for node in changed.nodes if node.id == "care").action = "仅在草稿中的行动"
    canvases.save_canvas("sop_service", draft["revision"], changed)
    assert "仅在草稿中的行动" not in flows.execution_prompt("service", flows.get_saved_flow("service"), None)
    assert canvases.get_canvas("sop_service")["execution"]["status"] == "active"


@pytest.mark.asyncio
async def test_publish_switches_live_version_and_failed_review_keeps_previous(monkeypatch):
    install()
    async def accepted(*args, **kwargs):
        return {"issues": []}
    monkeypatch.setattr(canvases, "resolve_content_sources", lambda flow: ([], {}))
    monkeypatch.setattr(canvases, "generate_json", accepted)
    canvas = definition()
    next(node for node in canvas.nodes if node.id == "care").action = "先请客户提供近照，再处理当前问题"
    canvases.save_canvas("sop_service", 2, canvas)
    result = await canvases.publish_canvas("sop_service", 3)
    assert result["published"] and result["workflow"]["execution"]["revision"] == 3
    assert "先请客户提供近照" in flows.execution_prompt("service", flows.get_saved_flow("service"), None)
    async def rejected(*args, **kwargs):
        return {"issues": [{"node_id": "care", "field": "action", "message": "行动有冲突", "suggestion": "修改行动"}]}
    monkeypatch.setattr(canvases, "generate_json", rejected)
    next(node for node in canvas.nodes if node.id == "care").action = "不能上线的行动"
    canvases.save_canvas("sop_service", 3, canvas)
    assert not (await canvases.publish_canvas("sop_service", 4))["published"]
    assert flows.get_saved_flow("service").revision == 3


def test_entry_rules_and_event_types_control_real_routing():
    snapshot = install()
    snapshot["entry_rule"] = {"required_tags": ["浙江省"], "events": ["first_contact"]}
    with canvases._session() as session:
        session.get(CanvasWorkflowModel, "sop_service").published_json = json.dumps(snapshot)
        session.commit()
    settings = {"service": True, "first_order": False, "seeding": False}
    assert eligible_sop_scopes(["服务中"], settings, event="first_contact") == []
    assert eligible_sop_scopes(["浙江省"], settings) == []
    assert eligible_sop_scopes(["浙江省"], settings, event="first_contact") == ["service"]


@pytest.mark.parametrize("cycle,interval", [("daily", 1), ("every_three_days", 3), ("weekly", 7)])
def test_published_schedule_drives_real_tasks_and_dedup(cycle, interval):
    install(definition(cycle=cycle))
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    assert touches.ensure_service_material_touch_tasks(now=now) == 1
    assert touches.ensure_service_material_touch_tasks(now=now) == 0
    with touches._database_session() as session:
        task = session.query(AgentWakeupModel).one()
        assert task.reason == "service_material:send" and task.due_at.hour == 2
    if interval > 1:
        assert touches._slots_for_date("service_material_touch", date(2026, 11, 5)) == ()
    assert touches._slots_for_date("service_material_touch", date(2026, 11, 4 + interval))


def test_imported_weekend_personalization_keeps_the_existing_merge_rule():
    from app.domains.orchestration.services.sop_canvas_import import sop_to_canvas
    from app.domains.orchestration.services.sop_flow_defaults import build_default_sop_packages
    package = next(item for item in build_default_sop_packages({}) if item["sop_scope"] == "service")
    canvas = sop_to_canvas("service", "服务 SOP", flows.default_flow(package), {})
    install(canvas)
    slots = touches._slots_for_date("service_material_touch", date(2026, 11, 8))
    afternoon = [slot for slot in slots if slot[1].hour == 14]
    assert len(afternoon) == 1
    assert touches._configured_touch_node("service_material_touch", afternoon[0][0]).schedule.purpose == "personalized_knowledge"


def test_unbound_or_unexecutable_snapshots_do_not_claim_active():
    snapshot = install()
    assert execution_status("unbound", snapshot)["status"] == "inactive"
    snapshot["definition"]["nodes"][0]["trigger_condition"] = "客户来自某个尚未支持的渠道"
    assert execution_status("sop_service", snapshot)["status"] == "inactive"


def test_multiple_business_branches_can_reach_the_same_handoff():
    canvas = definition()
    choose = next(node for node in canvas.nodes if node.id == "choose")
    choose.branches.extend([CanvasBranch(id="image", condition="发图询问品种转人工"),
                            CanvasBranch(id="identity", condition="质疑或确认AI身份转人工")])
    data = canvas.model_dump()
    data["nodes"].append({"id": "human", "type": "handoff", "name": "人工接待", "position": {"x": 0, "y": 0}})
    data["edges"].extend([
        {"id": "image_handoff", "source": "choose", "source_branch": "image", "target": "human"},
        {"id": "identity_handoff", "source": "choose", "source_branch": "identity", "target": "human"},
        {"id": "human_end", "source": "human", "target": "end"}])
    flow = compile_canvas("sop_service", install(CanvasDefinition.model_validate(data)))
    assert "human" in flows.dialogue_choices(flow, None)
    assert next(node for node in flow.steps if node.step_id == "human").handoff_enabled
    assert {edge.label for edge in flow.transitions if edge.to_step == "human"} == {"发图询问品种转人工", "质疑或确认AI身份转人工"}


def test_new_publication_blocks_outbound_prepared_with_old_branch_conditions(monkeypatch):
    install()
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)
    touches.ensure_service_material_touch_tasks(now=now)
    flow = flows.get_saved_flow("service")
    schedule = next(node.schedule for node in flow.steps if node.schedule)
    with touches._database_session() as session:
        task = session.query(AgentWakeupModel).one()
        task.status = "queued"
        task.checklist_json = json.dumps({"tags": ["服务中"], "schedule": schedule.model_dump(mode="json"),
                                          "flow_version": flows.execution_version(flow)})
        task_id = task.id
        session.commit()
    changed = definition()
    next(node for node in changed.nodes if node.id == "choose").branches[1].condition = "客户确认愿意接收且到了定时分享时间"
    install(changed, revision=3)
    monkeypatch.setattr(touches, "_touch_cancel_reason", lambda *args: None)
    assert not touches.validate_service_material_touch_before_send(f"service_material_touch:{task_id}")


@pytest.mark.asyncio
@pytest.mark.parametrize("eligible", [False, True])
async def test_due_canvas_task_checks_path_and_queues_a_single_link_card(monkeypatch, eligible):
    from app.integrations.eyun.services import message_risk_control_service
    install()
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)
    touches.ensure_service_material_touch_tasks(now=now)
    monkeypatch.setattr(touches, "_utcnow", lambda: now)
    monkeypatch.setattr(touches, "_touch_cancel_reason", lambda *args: None)
    async def path(*args, **kwargs):
        return eligible
    async def material(*args, **kwargs):
        return {"title": "手册", "content_reference": {"library": "care_manual", "item_id": "1"},
                "outbound_messages": [{"type": "link_card", "content": json.dumps({"title": "手册", "url": "https://example.test/manual"})}]}
    queued = []
    async def enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": 1}
    monkeypatch.setattr(touches, "scheduled_path_matches", path)
    monkeypatch.setattr(touches, "select_canvas_content", material)
    monkeypatch.setattr(message_risk_control_service, "enqueue_wechat_outbound", enqueue)
    assert await touches.process_due_service_material_touches(now=now) == int(eligible)
    assert len(queued) == int(eligible)
    if eligible:
        assert queued[0]["message_type"] == "link_card"
        assert queued[0]["source_batch_key"].startswith("service_material_touch:")
        monkeypatch.setattr(touches, "content_reference_is_available", lambda reference: True)
        assert touches.validate_service_material_touch_before_send(queued[0]["source_batch_key"])


@pytest.mark.asyncio
async def test_canvas_handoff_uses_the_existing_human_handoff_path(monkeypatch):
    from test_agent_harness_v2 import _message, _decision
    from app.domains.decisioning.services import agent_runtime
    from app.domains.customers.schemas.state import UserState
    data = definition().model_dump()
    node = next(item for item in data["nodes"] if item["id"] == "care")
    node.update(type="handoff", name="AI身份疑问转人工")
    install(CanvasDefinition.model_validate(data))
    async def generate(messages, **kwargs):
        return _decision(sop_node="service.care", final={"messages": [{"type": "text", "content": "请稍等。"}]})
    async def handoff(**kwargs):
        assert kwargs["decision"].sop_node == "service.care"
        assert kwargs["sop_scope"] == "service"
        return SimpleNamespace(need_human=True)
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    monkeypatch.setattr(agent_runtime, "_sop_node_handoff_reply", handoff)
    message = _message("你是AI吧？")
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id=message.user_id), workspace={"profile": {}})
    assert reply.need_human


@pytest.mark.asyncio
async def test_unexecutable_entry_cannot_replace_a_published_version(monkeypatch):
    install()
    canvas = definition()
    next(node for node in canvas.nodes if node.type == "start").trigger_condition += "\n仅限某个未接入的外部系统已确认的客户"
    canvases.save_canvas("sop_service", 2, canvas)
    async def accepted(*args, **kwargs):
        return {"issues": []}
    async def unavailable_rule(text):
        raise ValueError("暂不支持此外部系统条件")
    monkeypatch.setattr(canvases, "generate_json", accepted)
    monkeypatch.setattr(canvases, "resolve_content_sources", lambda flow: ([], {}))
    monkeypatch.setattr(canvases, "resolve_entry_rule", unavailable_rule)
    result = await canvases.publish_canvas("sop_service", 3)
    assert not result["published"]
    assert "外部系统" in result["review"]["issues"][0]["message"]
    assert flows.get_saved_flow("service").revision == 2


@pytest.mark.asyncio
async def test_real_agent_receives_and_uses_published_action(monkeypatch):
    from test_agent_harness_v2 import _message, _decision
    from app.domains.decisioning.services import agent_runtime
    from app.domains.customers.schemas.state import UserState
    install()
    async def generate(messages, **kwargs):
        assert any("先回答当前问题" in item["content"] for item in messages if item["role"] == "system")
        return _decision(sop_node="service.care", final={"messages": [{"type": "text", "content": "先检查植料内部是否仍潮湿，潮湿时暂缓浇水。"}]})
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    message = _message("植料还湿，要不要继续浇水？")
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id=message.user_id),
        workspace={"profile": {"customer_tags": ["服务中"]}})
    assert reply.metadata["agent_runtime"]["sop_node"] == "service.care"
    assert reply.metadata["agent_runtime"]["flow_version"] == flows.execution_version(flows.get_saved_flow("service"))


@pytest.mark.asyncio
async def test_scheduled_source_restricts_format_category_and_selected_ids(monkeypatch):
    schedule = next(node.schedule for node in compile_canvas("sop_service", install()).steps if node.schedule)
    monkeypatch.setattr(content, "list_content_sources", lambda libraries: [{"available": True, "items": [
        {"id": "image-1", "name": "图文", "category": "养护科普"},
        {"id": "image-2", "name": "其他图文", "category": "养护科普"}]}])
    async def choose(prompt, **kwargs):
        assert "image-1" in prompt and "image-2" not in prompt
        return {"item_id": "image-1"}
    monkeypatch.setattr(content, "generate_json", choose)
    monkeypatch.setattr(content, "get_agent_media", lambda item_id: {"format": "image", "copy_status": "ready", "copy_type": "养护科普"})
    material = await content.select_canvas_content(schedule, profile={}, local_date=date(2026, 11, 4))
    assert material["content_reference"] == {"library": "cloud_image", "item_id": "image-1"}
    monkeypatch.setattr(content, "get_agent_media", lambda item_id: {"format": "video", "copy_status": "ready", "copy_type": "养护科普"})
    assert await content.select_canvas_content(schedule, profile={}, local_date=date(2026, 11, 4)) is None
