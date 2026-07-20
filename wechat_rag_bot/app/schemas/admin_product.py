from pydantic import BaseModel, Field


class ProductSortRequest(BaseModel):
    sort_order: int = Field(ge=-1_000_000, le=1_000_000)


class ProductNoteRequest(BaseModel):
    internal_note: str = Field(default="", max_length=2000)
