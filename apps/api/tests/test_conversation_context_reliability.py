import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.conversations.services import conversation_context_service as history
from app.domains.conversations.services import conversation_progress_service as progress
from app.domains.conversations.services.conversation_service import _get_session, update_outbound_message_delivery
from app.domains.customers.schemas.memory import MemoryEventCreate
from app.domains.customers.schemas.state import UserState
from app.domains.customers.services import memory_retrieval_service as retrieval
from app.domains.customers.services.memory_consolidation_service import apply_memory_candidate
from app.domains.customers.services.memory_delivery_service import invalid_delivery_event_ids, record_delivered_message
from app.domains.customers.services.memory_dual_write_service import dual_write_conversation_event
from app.domains.customers.services.memory_event_service import append_memory_event, list_memory_events
from app.domains.customers.services.memory_extraction_service import extract_memory_candidates
from app.domains.customers.services.memory_identity_service import get_subject_for_identity
from app.domains.customers.services.memory_repository import get_memory_session
from app.domains.decisioning.schemas.agent import AgentTurnDecision, IssueProgress
from app.domains.decisioning.schemas.execution import AgentExecutionContext
from app.domains.decisioning.services.agent_prompt import build_turn_payload
from app.domains.decisioning.services.agent_tools import execute_agent_tool
from app.infrastructure.database.models import ConversationMessageModel, ConversationModel, MemoryEventModel


NOW = datetime(2026, 10, 18, 7, tzinfo=timezone.utc)
CID = "wechat:context-user:account-a"


@pytest.fixture(autouse=True)
def isolated_stores(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{tmp_path / 'memory.db'}")
    monkeypatch.setattr(get_settings(), "chat_log_db_url", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setattr(get_settings(), "memory_v2_write_enabled", True)
    with _get_session() as session:
        session.add(ConversationModel(conversation_id=CID, channel="wechat", user_id="context-user",
            tenant_id="tenant-a", session_id="account-a", owner_wc_id="account-a", created_at=NOW, updated_at=NOW))
        session.commit()


def incoming(text="还是发黄", **updates):
    fields = dict(trace_id="current-trace", channel="wechat", user_id="context-user",
        session_id="account-a", tenant_id="tenant-a", message=text, kb_id="kb_default")
    return NormalizedMessage(**{**fields, **updates})


def add_message(text, *, sender="customer", status=None, cid=CID, trace=None, metadata=None):
    with _get_session() as session:
        row = ConversationMessageModel(conversation_id=cid, sender_type=sender, content=text,
            delivery_status=status, trace_id=trace, metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            created_at=NOW)
        session.add(row)
        session.commit()
        return row.id


def memory_events():
    with get_memory_session() as session:
        return list(session.scalars(select(MemoryEventModel)))


@pytest.mark.asyncio
async def test_memory_follows_confirmed_sends_and_rechecks_revocation():
    assert dual_write_conversation_event(tenant_id="tenant-a", channel="wechat", external_user_id="context-user",
        owner_external_id="account-a", session_id="account-a", role="assistant", content="我会帮您登记。",
        source_id="draft-trace", trace_id="draft-trace", occurred_at=NOW) is None
    message_id = add_message("我会帮您登记。", sender="ai", status="queued", trace="draft-trace")
    for status in ("accepted", "unconfirmed", "failed"):
        update_outbound_message_delivery(message_id, status=status)
        assert memory_events() == []
    update_outbound_message_delivery(message_id, status="confirmed")
    update_outbound_message_delivery(message_id, status="confirmed")
    events = memory_events()
    assert len(events) == 1 and invalid_delivery_event_ids(events) == set()
    update_outbound_message_delivery(message_id, status="revoked")
    assert invalid_delivery_event_ids(events) == {events[0].id}
    reads = list_memory_events(tenant_id=events[0].tenant_id, subject_id=events[0].subject_id)
    assert await extract_memory_candidates(events=reads, trigger_event_id=events[0].id, use_llm=False) == []


@pytest.mark.asyncio
async def test_old_commitment_is_hidden_after_failed_delivery(monkeypatch):
    message_id = add_message("我会处理后告诉您结果。", sender="human", status="sent", trace="legacy-trace")
    record_delivered_message(message_id)
    subject = get_subject_for_identity(tenant_id="tenant-a", channel="wechat", owner_external_id="account-a", external_user_id="context-user")
    legacy = append_memory_event(MemoryEventCreate(event_uid="legacy-event", tenant_id="tenant-a", subject_id=subject.id,
        session_id="account-a", event_type="human_message", actor_type="human_agent", content={"text": "我会处理后告诉您结果。"},
        source_type="conversation_message", source_id=f"workbench:{message_id}", trace_id="legacy-trace", occurred_at=NOW)).event
    candidates = await extract_memory_candidates(events=[legacy], trigger_event_id=legacy.id, use_llm=False)
    assert candidates
    apply_memory_candidate(tenant_id="tenant-a", subject_id=subject.id, candidate=candidates[0])
    async def unavailable(*args, **kwargs):
        raise RuntimeError("Use local SQL evidence in this test")
    monkeypatch.setattr(retrieval, "embed_text", unavailable)
    context = await retrieval.retrieve_memory_context(tenant_id="tenant-a", subject_id=subject.id, query="之前答应跟进", as_of=NOW + timedelta(days=1))
    assert context.relevant_episodes
    update_outbound_message_delivery(message_id, status="failed")
    context = await retrieval.retrieve_memory_context(tenant_id="tenant-a", subject_id=subject.id, query="之前答应跟进", as_of=NOW + timedelta(days=1))
    assert context.relevant_episodes == [] and context.evidence == []


def test_uncertain_reply_keeps_full_text_and_status_for_short_followup():
    text = "这是还需要确认的处理建议。" * 40 + "您明天方便拍照吗？"
    message_id = add_message(text, sender="ai", status="accepted")
    update_outbound_message_delivery(message_id, status="unconfirmed")
    turns = history.get_conversation_context(CID, 20000)["turns"]
    payload = json.loads(build_turn_payload(customer_message="可以", customer_workspace={"recent_turns": turns}, event_context={}, tool_results=[]))
    assert payload["full_conversation"]["turns"][0]["content"] == text
    assert payload["full_conversation"]["turns"][0]["delivery_status"] == "unconfirmed"
    assert payload["turn_focus"]["previous_assistant_turn_indexes"] == [0]


@pytest.mark.asyncio
async def test_history_tool_searches_beyond_window_and_scopes_owner():
    older = add_message("上周已经停肥，盆土未干不浇水")
    add_message("另一个账号的停肥建议", cid="wechat:context-user:account-b")
    add_message("已经撤回的停肥建议", metadata={"revoked_at": "2026-10-18"})
    current = add_message("最近的其他聊天。" * 4000)
    context = AgentExecutionContext(message=incoming(), user_state=UserState(user_id="context-user"),
        workspace={"recent_turns": [{"message_id": current, "role": "customer", "content": "最近的其他聊天"}]})
    result = await execute_agent_tool(call_id="old", name="customer.get_context", arguments={"query": "停肥"}, context=context)
    assert [turn["message_id"] for turn in result.data["turns"]] == [older]
    assert "workspace" not in result.data
    page = await execute_agent_tool(call_id="page", name="customer.get_context", arguments={}, context=context)
    assert [turn["message_id"] for turn in page.data["turns"]] == [older]


def test_history_pagination_has_no_gaps():
    ids = [add_message(f"第{index}天的记录") for index in range(5)]
    page = history.search_conversation_history(CID, limit=2)
    assert [turn["message_id"] for turn in page["turns"]] == ids[-2:]
    page = history.search_conversation_history(CID, limit=3, before_message_id=page["next_before_message_id"])
    assert [turn["message_id"] for turn in page["turns"]] == ids[:3]
    assert not page["has_more"]


def test_progress_retains_grounded_steps_across_long_history_and_invalidates_revoked_source():
    source_id = add_message("已经停肥三天，叶尖还在发黄")
    update = IssueProgress(summary="客户已停肥三天，叶尖仍发黄，原因待核实。",
        evidence=[{"message_id": source_id, "quote": "已经停肥三天，叶尖还在发黄"}])
    assert progress.save_issue_progress(incoming(), update)
    add_message("后续其他聊天。" * 5000)
    assert progress.load_issue_progress(incoming())["summary"] == update.summary
    with _get_session() as session:
        row = session.get(ConversationMessageModel, source_id)
        row.metadata_json = json.dumps({"revoked_at": "2026-10-18"})
        session.commit()
    assert progress.load_issue_progress(incoming()) == {}


def test_progress_rejects_drafts_wrong_quotes_and_other_account():
    message_id = add_message("请先停肥观察", sender="ai", status="queued")
    other_id = add_message("已经停肥", cid="wechat:context-user:account-b")
    for source_id, quote in ((message_id, "请先停肥观察"), (other_id, "已经停肥"), ("current", "已经停肥")):
        update = IssueProgress(summary="客户已停肥", evidence=[{"message_id": source_id, "quote": quote}])
        assert not progress.save_issue_progress(incoming("还没处理"), update)


def test_progress_current_message_is_resolved_after_recording_and_does_not_match_null_ids():
    add_message("无关消息", trace="other-trace")
    update = IssueProgress(summary="客户已换盆，等待反馈", evidence=[{"message_id": "current", "quote": "已经换盆"}])
    assert progress.save_issue_progress(incoming("已经换盆"), update)
    assert progress.load_issue_progress(incoming()) == {}
    message_id = add_message("已经换盆", trace="current-trace")
    assert progress.load_issue_progress(incoming())["evidence"][0]["message_id"] == message_id


def test_progress_resolves_current_quote_from_any_message_in_merged_inbound_batch():
    first = add_message("已经停肥三天", trace="source-one")
    second = add_message("现在还是发黄", trace="source-two")
    message = incoming("已经停肥三天\n现在还是发黄", metadata={"conversation_message_ids": [first, second]})
    update = IssueProgress(summary="已停肥三天，仍在观察", evidence=[{"message_id": "current", "quote": "已经停肥三天"}])
    assert progress.save_issue_progress(message, update)
    assert progress.load_issue_progress(incoming())["evidence"][0]["message_id"] == first


def test_media_and_previous_reply_are_not_copied_into_payload_twice():
    workspace = {"recent_turns": [{"message_id": 7, "role": "customer", "content": "独有的图片识别内容"},
        {"message_id": 8, "role": "assistant", "content": "独有的上一轮追问"}],
        "recent_customer_media": [{"message_id": 7, "content": "独有的图片识别内容"},
            {"message_id": 2, "content": "窗口以外的重要旧图片"}]}
    raw = build_turn_payload(customer_message="好的", customer_workspace=workspace, event_context={}, tool_results=[])
    assert raw.count("独有的图片识别内容") == 1
    assert raw.count("独有的上一轮追问") == 1
    assert "窗口以外的重要旧图片" in raw


def test_media_details_survive_when_oldest_history_message_is_clipped():
    content = "首段特有的未重复条件。" + "识别出的重要条件。" * 2500
    raw = build_turn_payload(customer_message="继续", customer_workspace={
        "recent_turns": [{"message_id": 1, "role": "customer", "content": content}],
        "recent_customer_media": [{"message_id": 1, "content": content[:5000]}]}, event_context={}, tool_results=[])
    assert json.loads(raw)["customer_workspace"]["recent_customer_media"]


@pytest.mark.asyncio
async def test_final_reply_persists_progress_for_next_turn_without_changing_customer_reply():
    from app.domains.decisioning.services.agent_runtime import _finalize_reply
    add_message("已经停肥", trace="current-trace")
    turn = AgentTurnDecision(commercial_judgment="继续处理养护问题", relationship_purpose="承接处理进展",
        sop_node="general.reply", issue_progress={"summary": "客户已停肥，继续观察",
            "evidence": [{"message_id": "current", "quote": "已经停肥"}]},
        final_response={"messages": [{"type": "text", "content": "好的，先观察叶片后续变化。"}]})
    context = AgentExecutionContext(message=incoming("已经停肥"), user_state=UserState(user_id="context-user"), workspace={})
    reply = await _finalize_reply(decision=turn, context=context, usage={}, tool_results=[], quality_flags=[], attempt_trace=[])
    assert reply.answer == "好的，先观察叶片后续变化。"
    assert progress.load_issue_progress(incoming())["summary"] == "客户已停肥，继续观察"
    assert progress.load_issue_progress(incoming(session_id="account-b")) == {}
