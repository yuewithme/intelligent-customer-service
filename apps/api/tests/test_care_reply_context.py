import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.conversations.services import conversation_context_service as history
from app.domains.conversations.services import media_context_service
from app.domains.conversations.services.chat_orchestrator import _customer_workspace
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.agent import AgentTurnDecision, AgentVisibleMessage
from app.domains.decisioning.schemas.execution import AgentExecutionContext
from app.domains.decisioning.schemas.reply import OutboundMessage
from app.domains.decisioning.services import agent_reply_policy as policy, agent_runtime
from app.domains.decisioning.services.agent_prompt import build_turn_payload
from app.infrastructure.database.models import ConversationMessageModel
from app.integrations.eyun.services.message_risk_control_service import _outbound_messages


CID = "wechat:context-customer:owner-1"
NOW = datetime(2026, 10, 18, 6, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_log_db_url", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{tmp_path / 'profile.db'}")
    monkeypatch.setattr(agent_runtime, "get_saved_flow", lambda _: None)
    monkeypatch.setattr(agent_runtime, "is_sop_node_handoff_enabled", lambda *_: False)


def add_message(content, sender="customer", *, status=None, metadata=None, offset=0, cid=CID):
    with history._get_session() as session:
        session.add(ConversationMessageModel(conversation_id=cid, content=content, sender_type=sender,
            delivery_status=status, metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            created_at=NOW + timedelta(seconds=offset)))
        session.commit()


def incoming(text):
    return NormalizedMessage(trace_id="care-context", channel="api", user_id="context-customer",
        session_id="owner-1", message=text, kb_id="kb_default", metadata={"sop_scope": "general"})


def decision(content, **kwargs):
    return AgentTurnDecision(commercial_judgment="承接当前养护问题", relationship_purpose="帮助客户完成当前处理",
        sop_node="general.reply", final_response={"messages": [{"type": "text", "content": content}]}, **kwargs)


def payload(turns):
    workspace = _customer_workspace(message=incoming("继续"), user_state=UserState(user_id="context-customer"),
        profile_bundle={"profile": {}, "recent_memories": turns})
    return json.loads(build_turn_payload(customer_message="继续", customer_workspace=workspace,
        event_context={}, tool_results=[]))


def test_context_keeps_human_customer_and_scheduled_messages_and_filters_delivery():
    add_message("老叶发黄", offset=0)
    add_message("已发出的建议", "ai", status="accepted", offset=1)
    add_message("微信客户端人工追问", "human", status="sent", metadata={"origin": "wechat_client"}, offset=2)
    add_message("人工服务期间补充的浇水情况", offset=3)
    add_message("今天的养护科普", "system", status="confirmed", offset=4)
    for index, status in enumerate(("queued", "sending", "failed", "cancelled", "unconfirmed", "delivery_unknown"), 5):
        add_message(f"不可视为已发送：{status}", "ai", status=status, offset=index)
    add_message("已撤回的错误建议", "human", status="sent", metadata={"revoked_at": "2026-10-18"}, offset=12)
    add_message("别的账号的私有对话", cid="wechat:context-customer:owner-2", offset=13)
    context = history.get_conversation_context(CID, 20_000)
    assert [turn["content"] for turn in context["turns"]] == [
        "老叶发黄", "已发出的建议", "微信客户端人工追问", "人工服务期间补充的浇水情况", "今天的养护科普",
        "不可视为已发送：unconfirmed", "不可视为已发送：delivery_unknown"]
    assert len(context["delivery_notes"]) == 4
    assert context["turns"][-1]["delivery_status"] == "delivery_unknown"
    assert context["turns"][2]["sender_type"] == "human"


def test_long_turn_and_human_question_survive_workspace_and_prompt():
    text = "养护过程记录。" * 200 + "现在伤口还湿吗？"
    result = payload([{"role": "human", "content": text, "created_at": NOW.isoformat()}])
    assert result["full_conversation"]["turns"][0]["content"] == text
    assert result["turn_focus"]["previous_assistant_turn_indexes"] == [0]
    assert result["full_conversation"]["turns"][0]["created_at"] == NOW.isoformat()


def test_old_history_is_budgeted_and_marked_without_silent_per_message_cutoff():
    add_message("早期消息。" * 2500)
    add_message("最新记录。" * 2500 + "尾部关键条件", "human", status="sent", offset=1)
    result = payload(history.get_conversation_context(CID, 20_000)["turns"])
    assert result["full_conversation"]["content_chars"] == 20_000
    assert result["full_conversation"]["truncated_oldest"] is True
    assert result["full_conversation"]["turns"][-1]["content"].endswith("尾部关键条件")


def test_delivery_timestamp_preserves_actual_order():
    add_message("排队后才发出的回复", "ai", status="confirmed", metadata={
        "delivery_timestamps": {"accepted_at": (NOW + timedelta(seconds=10)).isoformat()}}, offset=0)
    add_message("客户在发送前补充的情况", offset=5)
    turns = history.get_conversation_context(CID, 20_000)["turns"]
    assert turns[0]["content"] == "客户在发送前补充的情况"
    assert turns[1]["content"] == "排队后才发出的回复"


def test_already_recorded_current_message_does_not_consume_history_budget():
    add_message("请补充环境情况", "human", status="sent")
    current = "这是本轮客户补充的环境情况。" * 2000
    add_message(current, offset=1)
    turns = history.get_conversation_context(CID, 20_000 + len(current))["turns"]
    result = json.loads(build_turn_payload(customer_message=current,
        customer_workspace={"recent_turns": turns}, event_context={}, tool_results=[]))
    assert [turn["content"] for turn in result["full_conversation"]["turns"]] == ["请补充环境情况"]


def test_transcript_cards_and_revoked_media_are_represented_truthfully():
    add_message("[语音]", metadata={"media": {"type": "audio", "recognition": {
        "status": "succeeded", "text": "三天前浇过水，现在还是湿的"}}})
    add_message("[链接卡片]", "human", status="sent", offset=1, metadata={
        "link_card": {"title": "春兰养护手册", "description": "其中包含换盆操作注意事项"}})
    add_message("[图片]", offset=2, metadata={"media": {"type": "image", "recognition": {"status": "failed"}}})
    add_message("[撤回图片]", offset=3, metadata={"revoked_at": "2026-10-18", "media": {
        "type": "image", "recognition": {"status": "succeeded", "text": "已撤回内容"}}})
    turns = history.get_conversation_context(CID, 20_000)["turns"]
    assert len(turns) == 3 and "三天前浇过水" in turns[0]["content"]
    assert "春兰养护手册" in turns[1]["content"] and "操作注意事项" in turns[1]["content"]
    assert turns[2]["content"] == "[图片内容尚未可靠识别]"
    assert all("已撤回内容" not in item["content"] for item in media_context_service.get_recent_customer_media(CID))


def test_missing_history_differs_from_history_with_only_unsent_drafts():
    assert history.get_conversation_context(CID, 20_000) is None
    add_message("待发送草稿", "ai", status="queued")
    assert history.get_conversation_context(CID, 20_000)["turns"] == []
    add_message("API 已返回的回复", "ai", cid="api:context-customer:owner-1")
    assert history.get_conversation_context("api:context-customer:owner-1", 20_000)["turns"][0]["content"] == "API 已返回的回复"


def test_current_photo_batch_is_separate_from_history_and_keeps_earlier_request():
    add_message("有啥拿不准的随时拍图发我", "ai", status="sent")
    add_message("我不知道这盆叫什么名，我拍照给你看看", offset=1)
    add_message("[图片]", offset=2, metadata={"media": {"type": "image", "recognition": {
        "status": "succeeded", "text": "花苞尚未完全开放，具体品种不确定"}}})
    turns = history.get_conversation_context(CID, 20_000)["turns"]
    current_id = turns[-1]["message_id"]
    message = incoming("[客户图片理解] 花苞尚未完全开放，具体品种不确定")
    message.metadata.update(conversation_message_ids=[current_id], image_count=1, recognized_image_count=1)
    result = json.loads(build_turn_payload(customer_message=message.message,
        customer_workspace={"recent_turns": turns, "recent_customer_media": media_context_service.get_recent_customer_media(CID)},
        event_context=agent_runtime._event_context(message), tool_results=[]))
    assert [turn["content"] for turn in result["full_conversation"]["turns"]] == [
        "有啥拿不准的随时拍图发我", "我不知道这盆叫什么名，我拍照给你看看"]
    assert result["turn_focus"]["current_customer_message_ids"] == [current_id]
    assert result["event_context"]["image_count"] == 1
    assert result["event_context"]["recognized_image_count"] == 1
    assert "花苞尚未完全开放" in result["customer_workspace"]["recent_customer_media"][0]["content"]


@pytest.mark.parametrize("old_ai", ["您先忙，后面随时喊我", "您觉得这种陪伴服务是不是更踏实？"])
def test_short_reply_focuses_latest_human_messages_instead_of_old_ai_closing(old_ai):
    add_message(old_ai, "ai", status="sent")
    add_message("和您分享莲瓣兰大雪素，您喜欢这类素花吗？", "human", status="sent", offset=1)
    add_message("这是它开花的视频", "human", status="sent", offset=2)
    add_message("我有一盆", offset=3)
    turns = history.get_conversation_context(CID, 20_000)["turns"]
    result = json.loads(build_turn_payload(customer_message="我有一盆", customer_workspace={"recent_turns": turns},
        event_context={"conversation_message_ids": [turns[-1]["message_id"]]}, tool_results=[]))
    indexes = result["turn_focus"]["previous_assistant_turn_indexes"]
    assert indexes == [1, 2]
    assert all(result["full_conversation"]["turns"][index]["sender_type"] == "human" for index in indexes)
    context = AgentExecutionContext(message=incoming("是的"), user_state=UserState(user_id="context-customer"),
        workspace={"recent_turns": turns})
    assert policy._affirmed_service_trial_close(context) is False


def test_current_batch_ids_preserve_identical_older_messages_and_remove_all_current_parts():
    result = json.loads(build_turn_payload(customer_message="好的\n这盆叫什么",
        customer_workspace={"recent_turns": [
            {"role": "customer", "content": "好的", "message_id": 1},
            {"role": "human", "content": "您现在想问哪一盆？", "message_id": 2},
            {"role": "customer", "content": "好的", "message_id": 3},
            {"role": "customer", "content": "这盆叫什么", "message_id": 4},
        ]}, event_context={"conversation_message_ids": [3, 4]}, tool_results=[]))
    assert [turn["message_id"] for turn in result["full_conversation"]["turns"]] == [1, 2]
    assert result["turn_focus"]["previous_assistant_turn_indexes"] == [1]


def test_reworded_closing_is_reviewed_without_forcing_sales_or_blocking_real_goodbye():
    context = AgentExecutionContext(message=incoming("我有一盆"), user_state=UserState(user_id="context-customer"),
        workspace={"recent_turns": [{"role": "assistant", "content": "不客气～那您先忙，后面兰花到了或者大棚搭好了，随时喊我。"}]})
    repeated = decision("好滴～那您先忙，后面兰花到了或者大棚搭好了，随时喊我。")
    assert "repeats_recent_assistant_content" in policy.sales_trajectory_violations(repeated, context)
    assert policy.guard_violations(repeated, context) == []
    assert "repeats_recent_assistant_content" not in policy.sales_trajectory_violations(decision("大雪素您已经有啦，现在养得怎么样？"), context)
    context.message = incoming("先不聊了，我忙去了")
    assert policy.sales_trajectory_violations(decision("好的，您先忙。"), context) == []


@pytest.mark.asyncio
async def test_repeated_goodbye_is_reconsidered_using_current_context(monkeypatch):
    calls = []
    async def generate(conversation, **kwargs):
        calls.append(conversation[:])
        content = "好滴～那您先忙，后面兰花到了或者大棚搭好了，随时喊我。" if len(calls) == 1 else "大雪素您已经有啦，现在养得怎么样？"
        return {"data": decision(content).model_dump()}
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    reply = await agent_runtime.run_sales_agent(message=incoming("我有一盆"),
        user_state=UserState(user_id="context-customer"), workspace={"recent_turns": [
            {"role": "assistant", "sender_type": "ai", "content": "不客气～那您先忙，后面兰花到了或者大棚搭好了，随时喊我。"},
            {"role": "assistant", "sender_type": "human", "content": "您喜欢莲瓣兰大雪素吗？"},
        ]})
    assert len(calls) == 2
    assert reply.answer == "大雪素您已经有啦，现在养得怎么样？"
    assert not reply.need_human
    assert "紧邻的人工和 AI 发言" in calls[1][-1]["content"]
    assert reply.metadata["agent_runtime"]["attempt_trace"][0]["outcome"] == "trajectory_rewrite_requested"


def test_length_review_covers_long_customer_messages_and_allows_requested_details():
    content = "这一段说明用于验证必要步骤能够完整保留，后续处理需要结合实际情况。" * 7
    context = AgentExecutionContext(message=incoming("这盆兰花放在阳台养了好几年，最近发黄，我已经发过照片了，接下来该怎么办"),
        user_state=UserState(user_id="context-customer"), workspace={})
    assert "reply_may_be_overexplained" in policy.quality_flags(decision(content), context)
    context.message = incoming("请详细讲讲完整步骤和注意事项")
    assert "reply_may_be_overexplained" not in policy.quality_flags(decision(content), context)
    assert policy.guard_violations(decision(content), context) == []


@pytest.mark.asyncio
async def test_soft_review_preserves_necessary_content_without_fragmenting_or_handoff(monkeypatch):
    content = "这段是客户需要的操作说明，包含必要的判断条件和操作顺序。" * 18 + "最后这项关键注意事项必须保留。"
    calls = []
    async def generate(*args, **kwargs):
        calls.append(1)
        return {"data": decision(content).model_dump()}
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    reply = await agent_runtime.run_sales_agent(message=incoming("详细说明完整步骤"),
        user_state=UserState(user_id="context-customer"), workspace={})
    assert len(calls) == 2 and not reply.need_human
    assert reply.answer == content and len(reply.outbound_messages) == 1
    assert len(_outbound_messages(reply.model_dump())) == 1


@pytest.mark.asyncio
async def test_prepared_delivery_is_not_cut_at_an_arbitrary_message_count():
    context = AgentExecutionContext(message=incoming("请把资料发给我"),
        user_state=UserState(user_id="context-customer"), workspace={},
        prepared={"material": [OutboundMessage(type="text", content=f"资料段落{index}", service_step="tutorial") for index in range(8)]})
    turn = decision("资料已准备好")
    turn.final_response.messages = [AgentVisibleMessage(type="prepared", ref="material")]
    reply = await agent_runtime._finalize_reply(decision=turn, context=context,
        usage={}, tool_results=[], quality_flags=[], attempt_trace=[])
    assert len(reply.outbound_messages) == 8 and reply.outbound_messages[-1].content == "资料段落7"


def test_service_invitation_and_long_member_intro_receive_soft_review_only():
    context = AgentExecutionContext(message=incoming("明白了，谢谢"),
        user_state=UserState(user_id="context-customer"), workspace={},
        prepared={"member": [OutboundMessage(type="text", content="会员活动介绍", service_step="member_day_intro")]})
    turn = decision("好的", service_handoff={"invitation_reason": "closing"})
    turn.final_response.messages.append(AgentVisibleMessage(type="prepared", ref="member"))
    assert "service_introduction_overlaps_handoff_invitation" in policy.quality_flags(turn, context)
    assert policy.guard_violations(turn, context) == []
