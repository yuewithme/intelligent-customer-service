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

    wechat_token: str = "change_me"
    wechat_app_id: str = "change_me"
    wechat_app_secret: str = "change_me"
    wechat_default_kb_id: str = "kb_default"

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
    rag_llm_provider: str = ""
    rag_llm_model: str = ""
    intent_llm_provider: str = ""
    intent_llm_model: str = ""
    intent_provider: str = "rule"
    intent_llm_enabled: bool = False
    intent_llm_fallback_threshold: float = Field(default=0.5, ge=0, le=1)
    talk_script_llm_provider: str = ""
    talk_script_llm_model: str = ""
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
