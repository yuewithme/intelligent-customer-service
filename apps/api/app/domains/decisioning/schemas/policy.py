from pydantic import BaseModel, Field


class PolicyDecision(BaseModel):
    route: str
    allowed: bool = True
    reason: str | None = None
    fallback_route: str | None = None
    original_route: str | None = None
    next_action: str | None = None
    action: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    prompt_block_ids: list[str] = Field(default_factory=list)
    context_policy: dict = Field(default_factory=dict)
    retrieval_policy: dict = Field(default_factory=dict)
