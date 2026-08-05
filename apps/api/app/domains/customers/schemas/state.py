from pydantic import BaseModel, Field


class UserState(BaseModel):
    user_id: str
    session_id: str | None = None
    customer_tags: list[str] = Field(default_factory=list)
    interested_products: list[str] = Field(default_factory=list)
    last_intent: str | None = None
    last_route: str | None = None
    last_template_id: str | None = None
    last_bot_message: str | None = None
    order_status: str = "unknown"
    risk_level: str = "normal"
    metadata: dict = Field(default_factory=dict)
