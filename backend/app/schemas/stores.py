from pydantic import BaseModel, EmailStr, Field


class StoreItem(BaseModel):
    owner_user_id: str
    owner_email: str
    access: str  # owner | granted


class StoresListResponse(BaseModel):
    stores: list[StoreItem]
    # Магазин по умолчанию для входа без сохранённого выбора: у сотрудника без собственного
    # WB-ключа и ровно с одним выданным магазином. Иначе None.
    default_store_owner_user_id: str | None = None


class StoreGrantRequest(BaseModel):
    grantee_email: str


class StoreGrantResponse(BaseModel):
    store_owner_user_id: str
    viewer_user_id: str
    status: str
    revoked_at: str | None


class OutgoingGrantItem(BaseModel):
    viewer_email: str
    viewer_user_id: str
    status: str
    revoked_at: str | None


class OutgoingGrantsResponse(BaseModel):
    grants: list[OutgoingGrantItem]


class StoreInviteCreateRequest(BaseModel):
    email: EmailStr


class StoreInviteCreatedResponse(BaseModel):
    id: str
    email: str
    status: str
    expires_at: str
    # Путь со ссылкой, например /invite/<token>. Токен отдаётся только в этом ответе.
    invite_url_path: str


class StoreInviteItem(BaseModel):
    id: str
    email: str
    status: str  # pending | expired | revoked
    expires_at: str
    created_at: str | None


class StoreManagerItem(BaseModel):
    viewer_user_id: str
    email: str
    granted_at: str | None


class StoreTeamResponse(BaseModel):
    managers: list[StoreManagerItem]
    invites: list[StoreInviteItem]


class StoreInviteLookupResponse(BaseModel):
    owner_email: str
    email: str
    status: str  # pending | accepted | revoked | expired
    expires_at: str


class StoreInviteAcceptRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class StoreInviteAcceptResponse(BaseModel):
    owner_user_id: str
    owner_email: str
    status: str

