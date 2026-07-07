from pydantic import BaseModel, Field


class TagResult(BaseModel):
    intent: str
    route: str
    segment: str = "unknown"
    emotion: str = "neutral"
    stage: str = "unknown"
    risk_level: str = "normal"
    confidence: float = Field(ge=0, le=1)
    secondary_intents: list[str] = Field(default_factory=list)
    entities: dict = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    reason: str | None = None
    source: str = "intent_service"

    @property
    def tags(self) -> list[str]:
        tags = [
            f"intent:{self.intent}",
            f"segment:{self.segment}",
        ]
        if self.emotion and self.emotion != "neutral":
            tags.append(f"emotion:{self.emotion}")
        if self.stage and self.stage != "unknown":
            tags.append(f"stage:{self.stage}")
        if self.risk_level and self.risk_level != "normal":
            tags.append(f"risk:{self.risk_level}")
        tags.extend(self.labels)
        return tags
