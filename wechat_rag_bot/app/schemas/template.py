from pydantic import BaseModel, Field


class TemplateItem(BaseModel):
    template_id: str
    name: str | None = None
    intent: str
    sub_intent: str | None = None
    stage: str = "unknown"
    scene: str | None = None
    customer_tags: list[str] = Field(default_factory=list)
    product_tags: list[str] = Field(default_factory=list)
    trigger_examples: list[str] = Field(default_factory=list)
    content: str
    variables: list[str] = Field(default_factory=list)
    next_action: str | None = None
    priority: int = 0
    status: str = "active"
    not_use_when: list[str] = Field(default_factory=list)
    version: int = 1


class TemplateReply(BaseModel):
    answer: str
    template_id: str
    next_action: str | None = None
    score: float | None = None
