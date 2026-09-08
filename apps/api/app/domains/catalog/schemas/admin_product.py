from pydantic import BaseModel, Field


class ProductSortRequest(BaseModel):
    sort_order: int = Field(ge=-1_000_000, le=1_000_000)


class ProductNoteRequest(BaseModel):
    internal_note: str = Field(default="", max_length=2000)


class ProductKnowledgePayload(BaseModel):
    item_id: str | None = Field(default=None, max_length=128)
    product_name: str = Field(min_length=1, max_length=256)
    aliases: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=128)
    flower_color: str = Field(default="", max_length=128)
    fragrance: str = Field(default="", max_length=128)
    flowering_status: str = Field(default="", max_length=128)
    price_budget: str = Field(default="", max_length=1000)
    care_scenes: str = Field(default="", max_length=2000)
    bloom_period: str = Field(default="", max_length=128)
    audience_tag: str = Field(default="", max_length=128)
    market_price: str = Field(default="", max_length=1000)
    highlighted_features: str = Field(default="", max_length=12000)
    sales_copy: str = Field(default="", max_length=12000)
    demand_tags: list[str] = Field(default_factory=list, max_length=32)
    seeding_scene: str = Field(default="", max_length=128)
    source_demand: str = Field(default="", max_length=256)
    spec_hint: str = Field(default="", max_length=128)


class ProductKnowledgeImportRequest(BaseModel):
    records: list[ProductKnowledgePayload] = Field(min_length=1, max_length=5000)
