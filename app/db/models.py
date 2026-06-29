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
