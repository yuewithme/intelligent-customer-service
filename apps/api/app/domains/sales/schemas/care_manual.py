from pydantic import BaseModel, Field, field_validator


class CareManualUpdateRequest(BaseModel):
    orchid_name: str = Field(default="", max_length=256)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    youzan_item_ids: list[str] = Field(default_factory=list, max_length=100)
    card_description: str = Field(default="", max_length=2000)
    sort_order: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    enabled: bool = True
    match_keywords: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("aliases", "youzan_item_ids", "match_keywords")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            cleaned = str(value).strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
        return result


class CareManualMatchRequest(BaseModel):
    query: str = Field(default="", max_length=512)
    product_name: str = Field(default="", max_length=512)
    youzan_item_id: str = Field(default="", max_length=128)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query", "product_name", "youzan_item_id")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()
