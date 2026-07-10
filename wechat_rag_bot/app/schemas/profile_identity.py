from pydantic import BaseModel, Field


class ProviderIdentityKey(BaseModel):
    tenant_id: str = "tenant_default"
    provider: str
    owner_external_id: str
    external_user_id: str


class ContactSnapshot(BaseModel):
    user_name: str | None = None
    alias_name: str | None = None
    nickname: str | None = None
    remark_name: str | None = None
    avatar_url: str | None = None
    province: str | None = None
    city: str | None = None
    label_ids: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str | None:
        return self.remark_name or self.nickname or self.alias_name


class ResolvedProfileIdentity(BaseModel):
    profile_user_id: str
    key: ProviderIdentityKey
    created: bool = False
