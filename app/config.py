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
    qdrant_vector_size: int = 1024
    qdrant_distance: str = "COSINE"
    qdrant_trust_env: bool = True

    llm_provider: str = "mock"
    llm_model: str = "deepseek-chat"
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    dashscope_api_key: str = ""

    embedding_provider: str = "mock"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"

    rag_top_k: int = Field(default=20, ge=1)
    rag_top_n: int = Field(default=5, ge=1)
    chunk_size: int = Field(default=600, ge=1)
    chunk_overlap: int = Field(default=100, ge=0)
    chunk_strategy: Literal["fixed", "adaptive"] = "fixed"
    markdown_heading_max_level: int = Field(default=6, ge=1, le=6)

    database_url: str = "sqlite:///./rag.db"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "data/uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
