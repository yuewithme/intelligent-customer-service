from pydantic import BaseModel, Field


class DemoChatRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=256)
    customer_name: str | None = Field(default=None, max_length=128)
