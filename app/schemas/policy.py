from pydantic import BaseModel


class PolicyDecision(BaseModel):
    route: str
    allowed: bool = True
    reason: str | None = None
    fallback_route: str | None = None
