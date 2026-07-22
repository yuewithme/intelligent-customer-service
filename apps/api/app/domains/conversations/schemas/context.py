from pydantic import BaseModel, Field


class ContextSelectionInput(BaseModel):
    profile: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)
    memories: list[dict] = Field(default_factory=list)
    memory_context: dict = Field(default_factory=dict)
    context_policy: dict = Field(default_factory=dict)


class ContextPackage(BaseModel):
    profile_summary: dict = Field(default_factory=dict)
    session_state: dict = Field(default_factory=dict)
    recent_turns: list[dict] = Field(default_factory=list)
    long_memory_summary: str = ""
    memory_facts: list[dict] = Field(default_factory=list)
    verified_business_facts: list[dict] = Field(default_factory=list)
    relevant_episodes: list[dict] = Field(default_factory=list)
    unresolved_memory_conflicts: list[dict] = Field(default_factory=list)
    memory_unknowns: list[str] = Field(default_factory=list)
