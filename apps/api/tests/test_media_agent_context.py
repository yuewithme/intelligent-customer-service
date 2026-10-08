import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.domains.conversations.services import media_context_service as context
from app.domains.conversations.services import chat_review_service as review
from app.infrastructure.database.models import ConversationMessageModel, EyunInboundMediaJobModel, EyunInboundBatchModel
from app.integrations.eyun.services import message_risk_control_service as queue


CID = "wechat:customer:bot"


@pytest.fixture
def media_db(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'messages.db'}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    with queue._get_session() as db:
        for index, media in enumerate([
            {"type": "image", "recognition": {"status": "succeeded", "text": "叶背可见白色虫体，疑似介壳虫"}},
            {"type": "audio", "recognition": {"status": "succeeded", "text": "已经养了三年，最近才开始发黄"}},
            {"type": "video", "recognition": {"status": "succeeded", "summary": "多盆兰花放在阳台", "transcript": "这盆要怎么处理", "visible_content": ["盆底积水"], "orchid_observations": ["老叶发黄"], "needs_clarification": True}},
        ]):
            db.add(ConversationMessageModel(
                conversation_id=CID, sender_type="customer", sender_id="customer", message_id=str(index),
                content="[媒体占位符]", metadata_json=json.dumps({"media": media}, ensure_ascii=False),
                created_at=now + timedelta(milliseconds=index),
            ))
        db.add(ConversationMessageModel(conversation_id="wechat:customer:other-bot", sender_type="customer",
            content="[图片]", metadata_json=json.dumps({"media": {"type": "image", "recognition": {"status": "succeeded", "text": "其他微信的私有内容"}}}), created_at=now))
        db.commit()
    yield now
    get_settings.cache_clear()


def test_media_evidence_survives_without_agent_memory_and_is_scoped_to_conversation(media_db):
    messages = context.get_recent_customer_media(CID)
    assert [message["type"] for message in messages] == ["image", "audio", "video"]
    text = json.dumps(messages, ensure_ascii=False)
    for value in ("白色虫体", "已经养了三年", "阳台", "这盆要怎么处理", "盆底积水", "老叶发黄", "不确定"):
        assert value in text
    assert "占位符" not in text and "其他微信" not in text
    assert context.get_recent_customer_media("wechat:other-customer:bot") == []


@pytest.mark.parametrize("media_type", ["image", "audio", "video"])
def test_unrecognized_media_does_not_leak_error_or_invent_content(media_type):
    message = {"content": "http://private-url?secret=value", "metadata": {"media": {
        "type": media_type, "recognition": {"status": "failed", "error": "internal error", "text": "unreliable content"},
    }}}
    assert "尚未可靠识别" in context.message_context_text(message)
    assert context.message_context_text(message) == review.message_text(message)


@pytest.mark.asyncio
async def test_image_result_is_saved_on_original_message_for_agent_and_human_review(media_db, monkeypatch):
    from app.integrations.ai.services import vision_service

    async def analyze(_):
        return vision_service.VisionAnalysis(category="orchid_general", summary="兰花正在开花",
            orchid_general=vision_service.OrchidGeneralAnalysis(visible_features=["白色花瓣"], uncertainties=["无法确定具体品种"]))

    monkeypatch.setattr(vision_service, "analyze_image", analyze)
    payloads = [{"messageType": "60002", "data": {"newMsgId": "0", "_image_url": "https://example.com/image.jpg"}}]
    prepared, *_ = await queue._prepare_inbound_content("[图片]", payloads)
    queue._save_image_recognitions(CID, payloads)
    assert prepared in context.get_recent_customer_media(CID)[0]["content"]
    assert "白色花瓣" in context.get_recent_customer_media(CID)[0]["content"]
    assert "无法确定具体品种" in context.get_recent_customer_media(CID)[0]["content"]
    with queue._get_session() as db:
        row = db.scalar(select(ConversationMessageModel).where(ConversationMessageModel.conversation_id == CID, ConversationMessageModel.message_id == "0"))
        assert row.content == "[媒体占位符]"
        assert prepared in review.message_text({"content": row.content, "metadata": json.loads(row.metadata_json)})


@pytest.mark.asyncio
@pytest.mark.parametrize("final_status", ["succeeded", "failed"])
async def test_followup_waits_for_media_and_resumes_when_processing_finishes(media_db, monkeypatch, final_status):
    from app.integrations.eyun.services import eyun_inbound_media_service as media_jobs

    clock = [media_db + timedelta(seconds=1)]
    monkeypatch.setattr(queue, "utcnow", lambda: clock[0])
    calls = []

    async def contact(**_):
        return {}

    async def generate(message):
        calls.append(message.message)
        return {"answer": "", "route": "agent"}

    async def finalize(**kwargs):
        return kwargs["chat_result"]

    monkeypatch.setattr(queue, "get_eyun_contact_snapshot", contact)
    monkeypatch.setattr(queue, "handle_chat", generate)
    monkeypatch.setattr(queue, "_finalize_eyun_handoff", finalize)
    with queue._get_session() as db:
        video = db.scalar(select(ConversationMessageModel).where(ConversationMessageModel.conversation_id == CID, ConversationMessageModel.message_id == "2"))
        video.metadata_json = json.dumps({"w_id": "wid", "owner_wc_id": "bot", "from_user": "customer", "message_type": "60003", "media": {"type": "video", "recognition": {"status": "processing"}}})
        job = EyunInboundMediaJobModel(conversation_message_id=video.id, dedup_key="video-job", media_type="video", status="processing", available_at=clock[0], created_at=clock[0], updated_at=clock[0])
        db.add(job)
        db.commit()
        job_id = job.id
    await queue.enqueue_eyun_inbound({"messageType": "60001", "wcId": "bot", "data": {"wId": "wid", "fromUser": "customer", "content": "刚才视频里那盆怎么办", "newMsgId": "followup"}})
    clock[0] += timedelta(seconds=10)
    await queue.process_due_eyun_inbound_batches()
    assert calls == []
    with queue._get_session() as db:
        assert db.scalar(select(EyunInboundBatchModel.status)) == "pending"
    if final_status == "succeeded":
        media_jobs._complete_media_job(job_id, "/static/media/video.mp4", recognition={"status": "succeeded", "summary": "老叶发黄，盆底积水"})
        await media_jobs._enqueue_media_agent_input(job_id, "[客户视频理解] 老叶发黄，盆底积水")
    else:
        with queue._get_session() as db:
            db.get(EyunInboundMediaJobModel, job_id).status = "failed"
            db.commit()
    clock[0] += timedelta(seconds=10)
    await queue.process_due_eyun_inbound_batches()
    assert len(calls) == 1 and "刚才视频里那盆怎么办" in calls[0]
    if final_status == "succeeded":
        assert "老叶发黄，盆底积水" in calls[0]


def test_media_wait_does_not_block_another_conversation_or_playback_repair(media_db):
    with queue._get_session() as db:
        video = db.scalar(select(ConversationMessageModel).where(ConversationMessageModel.conversation_id == CID, ConversationMessageModel.message_id == "2"))
        job = EyunInboundMediaJobModel(conversation_message_id=video.id, dedup_key="pending-video", media_type="video", status="pending", available_at=media_db, created_at=media_db, updated_at=media_db)
        db.add(job)
        db.commit()
        assert not queue._pause_inbound_for_media(0, {"from_user": "other-customer", "wc_id": "bot"})
        job.payload_json = json.dumps({"preserve_recognition": True})
        db.commit()
        assert not queue._pause_inbound_for_media(0, {"from_user": "customer", "wc_id": "bot"})
