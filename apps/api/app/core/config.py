from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    app_name: str = "wechat_rag_bot"
    app_public_base_url: str = Field(default="", alias="APP_PUBLIC_BASE_URL")
    api_auth_enabled: bool = True
    api_key: str = "change_me"
    mcp_api_key: str = ""
    mcp_allowed_hosts: list[str] = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "testserver",
        "150.158.52.233",
        "150.158.52.233:*",
    ]
    mcp_allowed_origins: list[str] = []
    admin_gate_enabled: bool = True
    admin_gate_password: str = ""
    admin_gate_test_password: str = ""
    admin_gate_secret: str = ""
    wechat_token: str = "change_me"
    wechat_app_id: str = "change_me"
    wechat_app_secret: str = "change_me"
    wechat_default_kb_id: str = "kb_default"
    eyun_base_url: str = ""
    eyun_authorization: str = ""
    eyun_wid: str = ""
    eyun_wc_id: str = Field(default="", alias="EYUN_WC_ID")
    eyun_login_monitor_interval_seconds: float = Field(
        default=30.0, ge=5.0, alias="EYUN_LOGIN_MONITOR_INTERVAL_SECONDS"
    )
    eyun_link_card_default_thumb_url: str = Field(
        default="", alias="EYUN_LINK_CARD_DEFAULT_THUMB_URL"
    )
    eyun_material_group_wc_id: str = Field(
        default="", alias="EYUN_MATERIAL_GROUP_WC_ID"
    )
    eyun_inbound_debounce_seconds: int = Field(
        default=5, ge=0, alias="EYUN_INBOUND_DEBOUNCE_SECONDS"
    )
    eyun_inbound_debounce_max_seconds: int = Field(
        default=15, ge=0, alias="EYUN_INBOUND_DEBOUNCE_MAX_SECONDS"
    )
    eyun_send_max_per_minute: int = Field(
        default=30, ge=1, alias="EYUN_SEND_MAX_PER_MINUTE"
    )
    eyun_send_min_interval_seconds: float = Field(
        default=2.1, ge=0, alias="EYUN_SEND_MIN_INTERVAL_SECONDS"
    )
    eyun_send_max_interval_seconds: float = Field(
        default=3.0, ge=0, alias="EYUN_SEND_MAX_INTERVAL_SECONDS"
    )
    eyun_opening_min_interval_seconds: float = Field(
        default=6.0, ge=1.0, alias="EYUN_OPENING_MIN_INTERVAL_SECONDS"
    )
    eyun_opening_max_interval_seconds: float = Field(
        default=10.0, ge=1.0, alias="EYUN_OPENING_MAX_INTERVAL_SECONDS"
    )
    eyun_opening_followup_min_seconds: float = Field(
        default=8.0, ge=1.0, alias="EYUN_OPENING_FOLLOWUP_MIN_SECONDS"
    )
    eyun_opening_followup_max_seconds: float = Field(
        default=15.0, ge=1.0, alias="EYUN_OPENING_FOLLOWUP_MAX_SECONDS"
    )
    eyun_opening_failure_pause_threshold: int = Field(
        default=2, ge=1, alias="EYUN_OPENING_FAILURE_PAUSE_THRESHOLD"
    )
    eyun_opening_pause_minutes: int = Field(
        default=30, ge=1, alias="EYUN_OPENING_PAUSE_MINUTES"
    )
    eyun_reply_jitter_min_seconds: int = Field(default=0, alias="EYUN_REPLY_JITTER_MIN_SECONDS")
    eyun_reply_jitter_max_seconds: int = Field(default=2, alias="EYUN_REPLY_JITTER_MAX_SECONDS")
    eyun_worker_poll_seconds: float = Field(default=1.0, alias="EYUN_WORKER_POLL_SECONDS")
    daily_touch_enabled: bool = Field(default=True, alias="DAILY_TOUCH_ENABLED")
    daily_touch_poll_seconds: float = Field(
        default=60.0, ge=5.0, alias="DAILY_TOUCH_POLL_SECONDS"
    )
    daily_touch_timezone: str = Field(
        default="Asia/Shanghai", alias="DAILY_TOUCH_TIMEZONE"
    )
    daily_touch_window_start: str = Field(
        default="08:00", alias="DAILY_TOUCH_WINDOW_START"
    )
    daily_touch_window_end: str = Field(
        default="23:00", alias="DAILY_TOUCH_WINDOW_END"
    )
    daily_touch_batch_size: int = Field(
        default=20, ge=1, le=200, alias="DAILY_TOUCH_BATCH_SIZE"
    )
    eyun_contact_missing_threshold: int = Field(
        default=3, ge=1, alias="EYUN_CONTACT_MISSING_THRESHOLD"
    )
    eyun_contact_refresh_delay_seconds: float = Field(
        default=15.0, ge=0, alias="EYUN_CONTACT_REFRESH_DELAY_SECONDS"
    )
    feishu_handoff_webhook_url: str = Field(
        default="", alias="FEISHU_HANDOFF_WEBHOOK_URL"
    )
    feishu_alert_webhook_url: str = Field(
        default="", alias="FEISHU_ALERT_WEBHOOK_URL"
    )
    evaluation_mode: bool = Field(default=False, alias="EVALUATION_MODE")
    memory_v2_write_enabled: bool = Field(
        default=False, alias="MEMORY_V2_WRITE_ENABLED"
    )
    memory_v2_llm_extraction_enabled: bool = Field(
        default=True, alias="MEMORY_V2_LLM_EXTRACTION_ENABLED"
    )
    memory_v2_min_confidence: float = Field(
        default=0.85, ge=0, le=1, alias="MEMORY_V2_MIN_CONFIDENCE"
    )
    memory_v2_worker_poll_seconds: float = Field(
        default=1.0, ge=0.05, alias="MEMORY_V2_WORKER_POLL_SECONDS"
    )
    memory_v2_job_lease_seconds: int = Field(
        default=600, ge=1, alias="MEMORY_V2_JOB_LEASE_SECONDS"
    )
    memory_v2_job_max_attempts: int = Field(
        default=3, ge=1, le=20, alias="MEMORY_V2_JOB_MAX_ATTEMPTS"
    )
    memory_v2_job_retry_base_seconds: int = Field(
        default=30, ge=1, alias="MEMORY_V2_JOB_RETRY_BASE_SECONDS"
    )
    qdrant_memory_collection: str = Field(
        default="customer_memory", alias="QDRANT_MEMORY_COLLECTION"
    )
    memory_v2_retrieval_top_k: int = Field(
        default=20, ge=1, le=100, alias="MEMORY_V2_RETRIEVAL_TOP_K"
    )
    memory_v2_context_max_facts: int = Field(
        default=8, ge=1, le=20, alias="MEMORY_V2_CONTEXT_MAX_FACTS"
    )
    memory_v2_context_max_episodes: int = Field(
        default=2, ge=0, le=10, alias="MEMORY_V2_CONTEXT_MAX_EPISODES"
    )
    memory_v2_context_max_evidence_per_episode: int = Field(
        default=4,
        ge=1,
        le=10,
        alias="MEMORY_V2_CONTEXT_MAX_EVIDENCE_PER_EPISODE",
    )
    memory_v2_shadow_enabled: bool = Field(
        default=False, alias="MEMORY_V2_SHADOW_ENABLED"
    )
    memory_v2_canary_enabled: bool = Field(
        default=False, alias="MEMORY_V2_CANARY_ENABLED"
    )
    memory_v2_canary_percent: int = Field(
        default=0, ge=0, le=100, alias="MEMORY_V2_CANARY_PERCENT"
    )
    memory_v2_gate_bypass_enabled: bool = Field(
        default=False, alias="MEMORY_V2_GATE_BYPASS_ENABLED"
    )
    memory_v2_shadow_min_samples: int = Field(
        default=100, ge=1, alias="MEMORY_V2_SHADOW_MIN_SAMPLES"
    )
    first_order_sales_flow_v2_enabled: bool | None = Field(
        default=None, alias="FIRST_ORDER_SALES_FLOW_V2_ENABLED"
    )

    @model_validator(mode="after")
    def default_sales_flow_v2_by_environment(self):
        if self.first_order_sales_flow_v2_enabled is None:
            self.first_order_sales_flow_v2_enabled = self.app_env.lower() not in {
                "prod",
                "production",
            }
        if (
            self.memory_v2_write_enabled
            and self.memory_v2_job_lease_seconds <= self.llm_timeout_seconds
        ):
            raise ValueError(
                "MEMORY_V2_JOB_LEASE_SECONDS must exceed LLM_TIMEOUT_SECONDS"
            )
        if self.memory_v2_canary_enabled and self.memory_v2_canary_percent <= 0:
            raise ValueError(
                "MEMORY_V2_CANARY_PERCENT must be positive when canary is enabled"
            )
        return self

    youzan_enabled: bool = False
    youzan_base_url: str = "https://open.youzanyun.com"
    youzan_access_token: str = ""
    youzan_kdt_id: str = ""
    youzan_product_search_method: str = "youzan.items.onsale.get"
    youzan_product_search_version: str = "3.0.0"
    youzan_order_search_method: str = "youzan.trades.sold.get"
    youzan_order_search_version: str = "4.0.0"
    youzan_customer_get_method: str = "youzan.scrm.customer.get"
    youzan_customer_get_version: str = "3.0.0"
    youzan_product_detail_enabled: bool = True
    youzan_product_detail_method: str = "youzan.item.get"
    youzan_product_detail_version: str = "3.0.0"
    youzan_inventory_method: str = "youzan.items.inventory.get"
    youzan_inventory_version: str = "3.0.0"
    youzan_follower_get_method: str = "youzan.users.weixin.follower.get"
    youzan_follower_get_version: str = "3.0.0"
    youzan_order_detail_enabled: bool = True
    youzan_order_detail_method: str = "youzan.trade.get"
    youzan_order_detail_version: str = "4.0.0"
    youzan_logistics_enabled: bool = False
    youzan_logistics_method: str = "youzan.logistics.expressbyorderno.search"
    youzan_logistics_version: str = "3.0.0"
    youzan_callback_enabled: bool = False
    youzan_client_id: str = ""
    youzan_client_secret: str = ""
    youzan_token_refresh_skew_seconds: int = Field(
        default=86400, ge=300, le=259200, alias="YOUZAN_TOKEN_REFRESH_SKEW_SECONDS"
    )
    youzan_product_page_path_template: str = ""
    youzan_product_h5_url_template: str = ""
    youzan_mini_program_app_id: str = ""
    youzan_mini_program_user_name: str = ""
    youzan_mini_program_display_name: str = ""
    youzan_mini_program_icon_url: str = ""
    youzan_order_page_path: str = ""
    youzan_order_card_title: str = "查看我的订单"
    youzan_order_card_thumb_url: str = ""
    youzan_product_sync_enabled: bool = True
    youzan_product_sync_interval_hours: int = Field(default=24, ge=1, le=168)
    youzan_product_sync_startup_delay_seconds: int = Field(default=30, ge=0, le=3600)
    youzan_product_sync_page_size: int = Field(default=100, ge=1, le=300)
    youzan_product_sync_detail_concurrency: int = Field(default=5, ge=1, le=20)
    youzan_order_sync_enabled: bool = True
    youzan_order_sync_interval_minutes: int = Field(default=5, ge=1, le=1440)
    youzan_order_sync_startup_delay_seconds: int = Field(default=45, ge=0, le=3600)
    youzan_order_sync_initial_lookback_days: int = Field(default=90, ge=1, le=730)
    youzan_order_sync_overlap_minutes: int = Field(default=10, ge=1, le=1440)
    youzan_order_sync_page_size: int = Field(default=100, ge=1, le=100)
    youzan_care_manual_method: str = "youzan.showcase.shopnote.list"
    youzan_care_manual_version: str = "1.0.0"
    youzan_care_manual_page_size: int = Field(default=20, ge=1, le=100)

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "knowledge_chunks"
    qdrant_knowledge_collection: str = "knowledge_chunks"
    qdrant_vector_size: int = 1024
    qdrant_distance: str = "COSINE"
    qdrant_trust_env: bool = True
    qdrant_upsert_batch_size: int = Field(default=128, ge=1)

    llm_provider: str = "mock"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = Field(default=180, ge=1)
    rag_llm_timeout_seconds: float = Field(default=60, ge=1)
    rag_llm_provider: str = ""
    rag_llm_model: str = ""
    rag_fast_llm_provider: str = ""
    rag_fast_llm_model: str = "qwen3.6-flash"
    reply_model_router_enabled: bool = Field(
        default=True, alias="REPLY_MODEL_ROUTER_ENABLED"
    )
    business_llm_provider: str = ""
    business_llm_model: str = ""
    persona_llm_provider: str = ""
    persona_llm_model: str = "qwen3.6-flash"
    persona_llm_timeout_seconds: float = Field(
        default=15, ge=1, alias="PERSONA_LLM_TIMEOUT_SECONDS"
    )
    persona_reply_enabled: bool = True
    persona_reply_temperature: float = Field(default=0.3, ge=0, le=1)
    profile_llm_provider: str = ""
    profile_llm_model: str = ""
    profile_analysis_prompt: str = ""
    review_llm_provider: str = ""
    review_llm_model: str = ""
    review_llm_reasoning_effort: Literal[
        "low", "medium", "high", "xhigh", "max"
    ] = Field(default="max", alias="REVIEW_LLM_REASONING_EFFORT")
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    dashscope_api_key: str = ""
    volcengine_api_key: str = ""
    ark_api_key: str = ""

    vision_enabled: bool = Field(default=False, alias="VISION_ENABLED")
    vision_model: str = Field(default="qwen3.7-plus", alias="VISION_MODEL")
    vision_ocr_enabled: bool = Field(default=True, alias="VISION_OCR_ENABLED")
    vision_ocr_model: str = Field(default="qwen3.5-ocr", alias="VISION_OCR_MODEL")
    vision_api_key: str = Field(default="", alias="VISION_API_KEY")
    vision_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="VISION_BASE_URL",
    )
    vision_timeout_seconds: float = Field(
        default=120, ge=1, alias="VISION_TIMEOUT_SECONDS"
    )
    vision_max_retries: int = Field(default=1, ge=0, le=3, alias="VISION_MAX_RETRIES")
    vision_min_confidence: float = Field(
        default=0.55, ge=0, le=1, alias="VISION_MIN_CONFIDENCE"
    )
    embedding_provider: str = "mock"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_batch_size: int = Field(default=16, ge=1)

    rag_top_k: int = Field(default=20, ge=1)
    rag_top_n: int = Field(default=5, ge=1)
    rag_knowledge_enabled: bool = False
    template_top_k: int = Field(default=5, ge=1)
    template_min_score: float = Field(default=0.5, ge=0, le=1)
    state_provider: str = "memory"
    rule_guard_enabled: bool = True
    debug_api_enabled: bool = True
    chunk_size: int = Field(default=600, ge=1)
    chunk_overlap: int = Field(default=100, ge=0)
    chunk_strategy: Literal["fixed", "adaptive"] = "fixed"
    markdown_heading_max_level: int = Field(default=6, ge=1, le=6)

    database_url: str = "sqlite:///./rag.db"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "data/uploads"
    sop_image_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    sop_video_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1)

    chat_log_enabled: bool = True
    chat_log_provider: str = "sqlite"
    chat_log_db_url: str = "sqlite:///./chat_logs.db"
    chat_log_retention_days: int = Field(default=30, ge=1)
    chat_log_max_message_length: int = Field(default=2000, ge=1)
    chat_log_max_answer_length: int = Field(default=4000, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
