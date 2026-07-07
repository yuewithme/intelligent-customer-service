from pydantic import BaseModel, Field


class ContextSelectionInput(BaseModel):
    profile: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)
    memories: list[dict] = Field(default_factory=list)
    context_policy: dict = Field(default_factory=dict)


class ContextPackage(BaseModel):
    profile_summary: dict = Field(default_factory=dict)
    session_state: dict = Field(default_factory=dict)
    recent_turns: list[dict] = Field(default_factory=list)
    long_memory_summary: str = ""
