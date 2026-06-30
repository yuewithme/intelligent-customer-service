from pydantic import BaseModel


class PolicyDecision(BaseModel):
    route: str
    allowed: bool = True
    reason: str | None = None
    fallback_route: str | None = None
    original_route: str | None = None
    next_action: str | None = None
