from pydantic import BaseModel, Field, field_validator


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


class TagPromptInput(BaseModel):
    block_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=12000)

    _strip_required = field_validator("title", "content")(_required_text)


class TagCreateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=256)
    prompts: list[TagPromptInput] = Field(default_factory=list, max_length=20)

    _strip_value = field_validator("value")(_required_text)


class TagUpdateRequest(TagCreateRequest):
    pass


class TagCategoryCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=128)
    prompt_rule: str = Field(default="", max_length=4000)
    ai_assignable: bool = True
    exclusive: bool = True

    _strip_name = field_validator("name")(_required_text)


class TagCategoryUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    prompt_rule: str = Field(default="", max_length=4000)
    ai_assignable: bool = True
    exclusive: bool = True

    _strip_name = field_validator("name")(_required_text)
