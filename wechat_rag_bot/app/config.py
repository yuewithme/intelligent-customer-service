from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    app_name: str = "wechat_rag_bot"
    api_auth_enabled: bool = True
    api_key: str = "change_me"
    admin_gate_enabled: bool = True
    admin_gate_password: str = ""
    admin_gate_secret: str = ""

    wechat_token: str = "change_me"
    wechat_app_id: str = "change_me"
    wechat_app_secret: str = "change_me"
    wechat_default_kb_id: str = "kb_default"
    eyun_base_url: str = ""
    eyun_authorization: str = ""
    eyun_wid: str = ""
    eyun_opening_text: str = (
        "我是萧岚苑的养兰师傅🌹咱们资料包含：图文，视频课程，一对一群版本等\n"
        "为了给您提供适合您的学习资料，请告诉我以下两点信息：\n\n"
        "1. 家里目前养了多少盆兰花？（还没养扣“0”😝）\n"
        "2. 具体养了哪些品种？"
    )
    eyun_opening_image_url: str = ""
    eyun_inbound_debounce_seconds: int = Field(default=60, alias="EYUN_INBOUND_DEBOUNCE_SECONDS")
    eyun_send_max_per_minute: int = Field(default=40, alias="EYUN_SEND_MAX_PER_MINUTE")
    eyun_send_min_interval_seconds: float = Field(default=1.6, alias="EYUN_SEND_MIN_INTERVAL_SECONDS")
    eyun_reply_jitter_min_seconds: int = Field(default=2, alias="EYUN_REPLY_JITTER_MIN_SECONDS")
    eyun_reply_jitter_max_seconds: int = Field(default=12, alias="EYUN_REPLY_JITTER_MAX_SECONDS")
    eyun_worker_poll_seconds: float = Field(default=1.0, alias="EYUN_WORKER_POLL_SECONDS")
    evaluation_mode: bool = Field(default=False, alias="EVALUATION_MODE")

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
    youzan_product_page_path_template: str = ""
    youzan_product_h5_url_template: str = ""
    youzan_mini_program_app_id: str = ""
    youzan_mini_program_user_name: str = ""
    youzan_mini_program_display_name: str = ""
    youzan_mini_program_icon_url: str = ""
    youzan_order_page_path: str = ""
    youzan_order_card_title: str = "查看我的订单"
    youzan_order_card_thumb_url: str = ""

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "knowledge_chunks"
    qdrant_knowledge_collection: str = "knowledge_chunks"
    qdrant_template_collection: str = "reply_templates"
    qdrant_intent_collection: str = "intent_examples"
    qdrant_vector_size: int = 1024
    qdrant_distance: str = "COSINE"
    qdrant_trust_env: bool = True
    qdrant_upsert_batch_size: int = Field(default=128, ge=1)

    llm_provider: str = "mock"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = Field(default=180, ge=1)
    rag_llm_provider: str = ""
    rag_llm_model: str = ""
    business_llm_provider: str = ""
    business_llm_model: str = ""
    intent_llm_provider: str = ""
    intent_llm_model: str = ""
    intent_provider: str = "rule"
    intent_llm_enabled: bool = False
    intent_llm_fallback_threshold: float = Field(default=0.5, ge=0, le=1)
    talk_script_llm_provider: str = ""
    talk_script_llm_model: str = ""
    profile_llm_provider: str = ""
    profile_llm_model: str = ""
    profile_analysis_prompt: str = ""
    review_llm_provider: str = ""
    review_llm_model: str = ""
    intent_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    intent_example_top_k: int = Field(default=5, ge=1)
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    dashscope_api_key: str = ""
    volcengine_api_key: str = ""
    ark_api_key: str = ""

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
