from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class YouzanProductModel(Base):
    __tablename__ = "youzan_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    price_cent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    h5_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, index=True, default=0)
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    youzan_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class YouzanProductSkuModel(Base):
    __tablename__ = "youzan_product_skus"
    __table_args__ = (UniqueConstraint("item_id", "sku_id", name="uq_youzan_product_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(128), index=True)
    sku_id: Mapped[str] = mapped_column(String(128), index=True)
    spec_name: Mapped[str] = mapped_column(String(512), default="默认规格")
    price_cent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku_code: Mapped[str | None] = mapped_column(String(256), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class YouzanProductSyncRunModel(Base):
    __tablename__ = "youzan_product_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    sku_count: Mapped[int] = mapped_column(Integer, default=0)
    detail_error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


class YouzanProductKnowledgeModel(Base):
    __tablename__ = "youzan_product_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    flower_color: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fragrance: Mapped[str | None] = mapped_column(String(128), nullable=True)
    flowering_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price_budget: Mapped[str | None] = mapped_column(Text, nullable=True)
    care_scenes: Mapped[str | None] = mapped_column(Text, nullable=True)
    bloom_period: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audience_tag: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    market_price: Mapped[str | None] = mapped_column(Text, nullable=True)
    highlighted_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    sales_copy: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CareManualCardModel(Base):
    __tablename__ = "care_manual_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    youzan_note_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    note_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    orchid_name: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    note_url: Mapped[str] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    youzan_status: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, index=True, default=0)
    match_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CareManualProductLinkModel(Base):
    __tablename__ = "care_manual_product_links"
    __table_args__ = (
        UniqueConstraint(
            "care_manual_card_id",
            "youzan_item_id",
            name="uq_care_manual_card_product",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    care_manual_card_id: Mapped[int] = mapped_column(Integer, index=True)
    youzan_item_id: Mapped[str] = mapped_column(String(128), index=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CareManualSyncRunModel(Base):
    __tablename__ = "care_manual_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    qualified_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    disabled_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


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


class IntentObservationModel(Base):
    __tablename__ = "intent_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    session_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    user_message: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text, default="[]")

    taxonomy_version: Mapped[str] = mapped_column(String(32), index=True, default="1.0")
    classifier_source: Mapped[str] = mapped_column(String(64), index=True, default="unknown")
    classifier_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classifier_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_prediction_json: Mapped[str] = mapped_column(Text, default="{}")
    candidate_labels_json: Mapped[str] = mapped_column(Text, default="[]")

    primary_domain: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    secondary_domains_json: Mapped[str] = mapped_column(Text, default="[]")
    primary_goal: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    secondary_goals_json: Mapped[str] = mapped_column(Text, default="[]")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    scope: Mapped[str] = mapped_column(String(32), index=True, default="ambiguous")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    predicted_route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    final_route: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    primary_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sales_stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="observed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IntentAnnotationModel(Base):
    __tablename__ = "intent_annotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("intent_observations.id"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    primary_domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    secondary_domains_json: Mapped[str] = mapped_column(Text, default="[]")
    primary_goal: Mapped[str | None] = mapped_column(String(128), nullable=True)
    secondary_goals_json: Mapped[str] = mapped_column(Text, default="[]")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    annotator_id: Mapped[str] = mapped_column(String(128), index=True)
    taxonomy_version: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunInboundBatchModel(Base):
    __tablename__ = "eyun_inbound_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    w_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    wc_id: Mapped[str] = mapped_column(String(256), index=True)
    target_wc_id: Mapped[str] = mapped_column(String(256), index=True)
    from_user: Mapped[str | None] = mapped_column(String(256), nullable=True)
    from_group: Mapped[str | None] = mapped_column(String(256), nullable=True)
    account: Mapped[str | None] = mapped_column(String(256), nullable=True)
    message_type: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunInboundMessageModel(Base):
    __tablename__ = "eyun_inbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    batch_key: Mapped[str] = mapped_column(String(512), index=True)
    content: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunOutboundMessageModel(Base):
    __tablename__ = "eyun_outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    w_id: Mapped[str] = mapped_column(String(256), index=True)
    wc_id: Mapped[str] = mapped_column(String(256), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_batch_key: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True)
    conversation_message_id: Mapped[int | None] = mapped_column(
        Integer, index=True, nullable=True
    )
    depends_on_outbound_id: Mapped[int | None] = mapped_column(
        Integer, index=True, nullable=True
    )
    material_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    bulk_job_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunSendRateModel(Base):
    __tablename__ = "eyun_send_rates"

    w_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunImagePromptRateModel(Base):
    __tablename__ = "eyun_image_prompt_rates"

    w_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    wc_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunMediaMaterialModel(Base):
    __tablename__ = "eyun_media_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    media_type: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    raw_xml: Mapped[str] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_w_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    source_wc_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="ready")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunBulkSendJobModel(Base):
    __tablename__ = "eyun_bulk_send_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    w_id: Mapped[str] = mapped_column(String(256), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    user_display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default="tenant_default")
    status: Mapped[str] = mapped_column(String(64), index=True, default="ai_active")
    owner_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_ticket_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    hidden_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(256), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    sender_type: Mapped[str] = mapped_column(String(32), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ActivityModel(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="published")
    enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    ai_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(128))
    updated_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ActivitySendLogModel(Base):
    __tablename__ = "activity_send_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(256), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    trigger_mode: Mapped[str] = mapped_column(String(16), index=True)
    operator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outbound_message_ids_json: Mapped[str] = mapped_column(Text, default="[]")
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
    active_opportunity_json: Mapped[str] = mapped_column(Text, default="{}")
    basic_info_json: Mapped[str] = mapped_column(Text, default="{}")
    last_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_template_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    friend_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemorySubjectModel(Base):
    __tablename__ = "memory_subjects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_memory_subject_tenant_id"),
        Index("ix_memory_subject_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


class MemoryIdentityModel(Base):
    __tablename__ = "memory_identities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "channel",
            "owner_external_id",
            "external_user_id",
            name="uq_memory_external_identity",
        ),
        Index(
            "ix_memory_identity_subject_scope", "tenant_id", "subject_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    owner_external_id: Mapped[str] = mapped_column(String(256), default="")
    external_user_id: Mapped[str] = mapped_column(String(256), index=True)
    identity_source: Mapped[str] = mapped_column(String(64), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryEventModel(Base):
    __tablename__ = "memory_events"
    __table_args__ = (
        Index(
            "ix_memory_event_subject_time",
            "tenant_id",
            "subject_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="memory.v1")
    event_uid: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        String(256), index=True, nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(32), index=True)
    content_json: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(512), index=True)
    trace_id: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sensitivity: Mapped[str] = mapped_column(String(32), index=True, default="internal")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


class MemoryFactModel(Base):
    __tablename__ = "memory_facts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_id",
            "fact_uid",
            "version",
            name="uq_memory_fact_version",
        ),
        Index(
            "ix_memory_fact_active_scope",
            "tenant_id",
            "subject_id",
            "status",
            "fact_key",
            "valid_from",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_uid: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True
    )
    fact_key: Mapped[str] = mapped_column(String(128), index=True)
    fact_value_json: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    supersedes_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_facts.id"), index=True, nullable=True
    )
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryFactEvidenceModel(Base):
    __tablename__ = "memory_fact_evidence"
    __table_args__ = (
        UniqueConstraint(
            "fact_id", "event_id", name="uq_memory_fact_evidence"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_id: Mapped[int] = mapped_column(
        ForeignKey("memory_facts.id"), index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("memory_events.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryEpisodeModel(Base):
    __tablename__ = "memory_episodes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_id",
            "episode_uid",
            "version",
            name="uq_memory_episode_version",
        ),
        Index(
            "ix_memory_episode_active_scope",
            "tenant_id",
            "subject_id",
            "status",
            "started_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_uid: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True
    )
    episode_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")
    embedding_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryEpisodeEventModel(Base):
    __tablename__ = "memory_episode_events"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "event_id", name="uq_memory_episode_event"
        ),
        UniqueConstraint(
            "episode_id", "position", name="uq_memory_episode_position"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("memory_episodes.id"), index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("memory_events.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryJobModel(Base):
    __tablename__ = "memory_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_memory_job_dedup"),
        Index("ix_memory_job_available", "status", "available_at", "id"),
        Index("ix_memory_job_subject", "tenant_id", "subject_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str] = mapped_column(String(512))
    trigger_event_id: Mapped[int] = mapped_column(
        ForeignKey("memory_events.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


class MemoryShadowRunModel(Base):
    __tablename__ = "memory_shadow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "trace_id", name="uq_memory_shadow_trace"),
        Index("ix_memory_shadow_tenant_time", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True, nullable=True
    )
    trace_id: Mapped[str] = mapped_column(String(128))
    query_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    injected: Mapped[bool] = mapped_column(Boolean, default=False)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    scope_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_business_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryRolloutGateModel(Base):
    __tablename__ = "memory_rollout_gates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "evaluation_version", name="uq_memory_rollout_evaluation"
        ),
        Index("ix_memory_rollout_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    evaluation_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    sample_count: Mapped[int] = mapped_column(Integer)
    retrieval_success_rate: Mapped[float] = mapped_column(Float)
    retrieval_recall_at_5: Mapped[float] = mapped_column(Float)
    temporal_accuracy: Mapped[float] = mapped_column(Float)
    evidence_grounding: Mapped[float] = mapped_column(Float)
    scope_violations: Mapped[int] = mapped_column(Integer)
    verified_business_violations: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[str] = mapped_column(String(128))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryFeedbackModel(Base):
    __tablename__ = "memory_feedback"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_id", name="uq_memory_feedback_request"),
        Index("ix_memory_feedback_subject_time", "tenant_id", "subject_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("memory_subjects.id"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(128))
    feedback_type: Mapped[str] = mapped_column(String(32), index=True)
    target_fact_id: Mapped[int] = mapped_column(ForeignKey("memory_facts.id"), index=True)
    correction_event_id: Mapped[int] = mapped_column(
        ForeignKey("memory_events.id"), index=True
    )
    replacement_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_facts.id"), index=True, nullable=True
    )
    reason_code: Mapped[str] = mapped_column(String(128))
    operator_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MemoryPurgeAuditModel(Base):
    __tablename__ = "memory_purge_audits"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_id", name="uq_memory_purge_request"),
        Index("ix_memory_purge_tenant_time", "tenant_id", "requested_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    identities_deleted: Mapped[int] = mapped_column(Integer, default=0)
    events_deleted: Mapped[int] = mapped_column(Integer, default=0)
    facts_deleted: Mapped[int] = mapped_column(Integer, default=0)
    episodes_deleted: Mapped[int] = mapped_column(Integer, default=0)
    jobs_deleted: Mapped[int] = mapped_column(Integer, default=0)
    feedback_deleted: Mapped[int] = mapped_column(Integer, default=0)
    shadow_runs_deleted: Mapped[int] = mapped_column(Integer, default=0)
    legacy_rows_deleted: Mapped[int] = mapped_column(Integer, default=0)
    vector_points_deleted: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


class MemoryProcedureCandidateModel(Base):
    __tablename__ = "memory_procedure_candidates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "candidate_uid", name="uq_memory_procedure_candidate"
        ),
        Index("ix_memory_procedure_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    candidate_uid: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    proposal: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    proposed_by: Mapped[str] = mapped_column(String(128))
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )


class MemoryProcedureEvidenceModel(Base):
    __tablename__ = "memory_procedure_evidence"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "feedback_id", name="uq_memory_procedure_evidence"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("memory_procedure_candidates.id"), index=True
    )
    feedback_id: Mapped[int] = mapped_column(
        ForeignKey("memory_feedback.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunContactModel(Base):
    __tablename__ = "eyun_contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_account_id", "wc_id", name="uq_eyun_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default="tenant_default")
    owner_account_id: Mapped[str] = mapped_column(String(256), index=True)
    wc_id: Mapped[str] = mapped_column(String(256), index=True)
    current_w_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remark_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wechat_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    friend_added_on: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    discovery_source: Mapped[str] = mapped_column(String(32), index=True, default="polling")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class YouzanIdentityBindingModel(Base):
    __tablename__ = "youzan_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "channel",
            "external_user_id",
            "kdt_id",
            name="uq_youzan_identity_binding",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    external_user_id: Mapped[str] = mapped_column(String(256), index=True)
    kdt_id: Mapped[str] = mapped_column(String(64), index=True)
    yz_uid: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    buyer_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    yz_open_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    fans_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    weixin_openid: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    union_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mobile_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class YouzanEventReceiptModel(Base):
    __tablename__ = "youzan_event_receipts"

    msg_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    kdt_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    event_status: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class YouzanToolCallAuditModel(Base):
    __tablename__ = "youzan_tool_call_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    caller: Mapped[str] = mapped_column(String(64), index=True, default="mcp")
    customer_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class HandoffNotificationSettingModel(Base):
    __tablename__ = "handoff_notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_contact_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    message_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UnpurchasedSopModel(Base):
    __tablename__ = "unpurchased_sops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), default="未购SOP")
    enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    send_window_start: Mapped[str] = mapped_column(String(5), default="09:00")
    send_window_end: Mapped[str] = mapped_column(String(5), default="20:00")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    contact_poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=120)
    contact_missing_threshold: Mapped[int] = mapped_column(Integer, default=3)
    baseline_initialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contact_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UnpurchasedSopStepModel(Base):
    __tablename__ = "unpurchased_sop_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sop_id: Mapped[int] = mapped_column(Integer, index=True)
    day_offset: Mapped[int] = mapped_column(Integer, index=True)
    send_time: Mapped[str] = mapped_column(String(5))
    send_time_start: Mapped[str] = mapped_column(String(5), default="09:00")
    send_time_end: Mapped[str] = mapped_column(String(5), default="09:00")
    message_type: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UnpurchasedSopEnrollmentModel(Base):
    __tablename__ = "unpurchased_sop_enrollments"
    __table_args__ = (
        UniqueConstraint("sop_id", "contact_id", "friend_added_on", name="uq_unpurchased_enrollment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sop_id: Mapped[int] = mapped_column(Integer, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, index=True)
    friend_added_on: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="active")
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UnpurchasedSopDeliveryModel(Base):
    __tablename__ = "unpurchased_sop_deliveries"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "step_id", name="uq_unpurchased_delivery"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(Integer, index=True)
    step_id: Mapped[int] = mapped_column(Integer, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="scheduled")
    message_type: Mapped[str] = mapped_column(String(32))
    content_snapshot: Mapped[str] = mapped_column(Text)
    preview_url_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    messages_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    outbound_message_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    outbound_message_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CustomerLevelProfileModel(Base):
    __tablename__ = "customer_level_profiles"

    level: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    min_score: Mapped[float] = mapped_column(Float, default=1.0)
    default_route: Mapped[str] = mapped_column(String(64), default="rag_answer")
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerLevelRuleModel(Base):
    __tablename__ = "customer_level_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    rule_type: Mapped[str] = mapped_column(String(64), default="keyword_any")
    pattern: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_label: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PromptBlockModel(Base):
    __tablename__ = "prompt_blocks"

    block_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerLevelPromptBindingModel(Base):
    __tablename__ = "customer_level_prompt_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    prompt_block_id: Mapped[str] = mapped_column(String(128), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TagPromptBindingModel(Base):
    __tablename__ = "tag_prompt_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[str] = mapped_column(String(64), index=True)
    tag_value: Mapped[str] = mapped_column(String(256), index=True)
    prompt_block_id: Mapped[str] = mapped_column(String(128), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TagCategoryModel(Base):
    __tablename__ = "tag_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    prompt_rule: Mapped[str] = mapped_column(Text, default="")
    ai_assignable: Mapped[bool] = mapped_column(Boolean, default=True)
    exclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class TagDefinitionModel(Base):
    __tablename__ = "tag_definitions"
    __table_args__ = (UniqueConstraint("value", name="uq_tag_definition_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(String(256), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class TagCatalogMetaModel(Base):
    __tablename__ = "tag_catalog_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


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
    sales_stage: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    sales_action: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    branch_code: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    required_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    exclude_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    required_fact_keys_json: Mapped[str] = mapped_column(Text, default="[]")
    variables_json: Mapped[str] = mapped_column(Text, default="[]")
    priority: Mapped[int] = mapped_column(Integer, default=0)
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


class OrchidCategoryModel(Base):
    __tablename__ = "orchid_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    category_description: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrchidVarietyModel(Base):
    __tablename__ = "orchid_varieties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_name: Mapped[str] = mapped_column(String(128), index=True)
    variety_name: Mapped[str] = mapped_column(String(256), index=True)
    primary_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    aliases_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    origin_area: Mapped[str | None] = mapped_column(String(256), nullable=True)
    history_background: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suitable_level: Mapped[str | None] = mapped_column(String(128), nullable=True)
    base_spec: Mapped[str | None] = mapped_column(String(256), nullable=True)
    base_price_text: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_basic_info: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrchidVarietyTraitModel(Base):
    __tablename__ = "orchid_variety_traits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variety_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variety_name: Mapped[str] = mapped_column(String(256), index=True)
    category_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    trait_type: Mapped[str] = mapped_column(String(128), index=True)
    trait_value: Mapped[str] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrchidValuePointModel(Base):
    __tablename__ = "orchid_value_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variety_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variety_name: Mapped[str] = mapped_column(String(256), index=True)
    category_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    value_type: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrchidSkuModel(Base):
    __tablename__ = "orchid_skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    variety_name: Mapped[str] = mapped_column(String(256), index=True)
    seedling_count: Mapped[str | None] = mapped_column(String(128), nullable=True)
    package_spec: Mapped[str | None] = mapped_column(String(256), nullable=True)
    flower_bud_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_text: Mapped[str | None] = mapped_column(String(256), nullable=True)


class OrchidCommonKnowledgeModel(Base):
    __tablename__ = "orchid_common_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    knowledge_category: Mapped[str] = mapped_column(String(256), index=True)
    knowledge_type: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    applies_to_category: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    content: Mapped[str] = mapped_column(Text)


class OrchidSalesCopyModel(Base):
    __tablename__ = "orchid_sales_copy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    writer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    variety_name: Mapped[str] = mapped_column(String(256), index=True)
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_background: Mapped[str | None] = mapped_column(Text, nullable=True)
    leaf_posture: Mapped[str | None] = mapped_column(Text, nullable=True)
    petal_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    flower_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    fragrance: Mapped[str | None] = mapped_column(Text, nullable=True)
    flowering_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    care_difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_scene: Mapped[str | None] = mapped_column(Text, nullable=True)
    selling_points: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrchidHotBreakdownModel(Base):
    __tablename__ = "orchid_hot_breakdowns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variety_name: Mapped[str] = mapped_column(String(256), index=True)
    category_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    status_history_supply_price_authenticity: Mapped[str | None] = mapped_column(Text, nullable=True)
    aesthetic_traits: Mapped[str | None] = mapped_column(Text, nullable=True)
    cultivation_care: Mapped[str | None] = mapped_column(Text, nullable=True)
    consensus_reputation: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrchidKnowledgeChunkModel(Base):
    __tablename__ = "orchid_knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_table: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(128), index=True)
    variety_name: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(128), index=True)
    chunk_title: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
