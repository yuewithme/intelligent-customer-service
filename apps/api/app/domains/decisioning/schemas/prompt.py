from pydantic import BaseModel, Field

from app.domains.conversations.schemas.context import ContextPackage


class PromptBuildInput(BaseModel):
    prompt_block_ids: list[str] = Field(default_factory=list)
    templates: list[str] = Field(default_factory=list)
    context: ContextPackage = Field(default_factory=ContextPackage)
    knowledge_snippets: list[dict] = Field(default_factory=list)
    user_message: str
