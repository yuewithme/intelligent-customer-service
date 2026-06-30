from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ChatLogModel(Base):
    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    session_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    kb_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    permission: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user_message: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    route: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    reply_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_intent: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True
    )
    secondary_intents_json: Mapped[str] = mapped_column(Text, default="[]")
    sales_stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    template_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    template_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(256), nullable=True)

    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    stage_latencies_json: Mapped[str] = mapped_column(Text, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    need_human: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="success")
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UserProfileModel(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default="tenant_default")
    channel: Mapped[str] = mapped_column(String(64), default="api")
    current_stage: Mapped[str] = mapped_column(String(128), default="unknown")
    risk_level: Mapped[str] = mapped_column(String(64), default="normal")
    is_human_handoff: Mapped[bool] = mapped_column(Boolean, default=False)
    human_ticket_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    human_handoff_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    human_handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    product_interests_json: Mapped[str] = mapped_column(Text, default="[]")
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    preference_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pain_points_json: Mapped[str] = mapped_column(Text, default="[]")
    last_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_template_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ConversationMemoryModel(Base):
    __tablename__ = "conversation_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default="tenant_default")
    session_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProfileEventModel(Base):
    __tablename__ = "profile_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default="tenant_default")
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    before_json: Mapped[str] = mapped_column(Text, default="{}")
    after_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SceneIndexModel(Base):
    __tablename__ = "scene_index"

    scene_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scene_name: Mapped[str] = mapped_column(String(256))
    scene_definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    enter_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    typical_user_messages: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclude_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")


class QuestionClusterModel(Base):
    __tablename__ = "question_cluster"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(String(32), index=True)
    sub_scene_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    standard_question: Mapped[str] = mapped_column(Text)
    core_intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_question_aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    positive_examples: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_examples: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclude_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    confusable_questions: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_template_id: Mapped[str] = mapped_column(String(64), index=True)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.75)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")


class TemplateLibraryModel(Base):
    __tablename__ = "template_library"

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    template_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    answer_default: Mapped[str] = mapped_column(Text)
    answer_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    need_slot_filling: Mapped[str] = mapped_column(String(32), default="no")
    handoff_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TalkScriptMatchLogModel(Base):
    __tablename__ = "talk_script_match_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    user_message: Mapped[str] = mapped_column(Text)
    normalized_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    candidate_question_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    matched_question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    need_slot_filling: Mapped[bool] = mapped_column(Boolean, default=False)
    need_human: Mapped[bool] = mapped_column(Boolean, default=False)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
