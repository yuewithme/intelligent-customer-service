from pydantic import BaseModel, Field


class YouzanCredentialsUpdateRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: str = Field(min_length=1, max_length=512, repr=False)
    kdt_id: str = Field(min_length=1, max_length=64)
