from pydantic import BaseModel, Field


class CustomerLevelResult(BaseModel):
    level: str = "unknown"
    label: str | None = None
    route: str = "rag_answer"
    confidence: float = Field(default=0.0, ge=0, le=1)
    score: float = 0.0
    matched_evidence: list[str] = Field(default_factory=list)
    handoff_reason: str | None = None
