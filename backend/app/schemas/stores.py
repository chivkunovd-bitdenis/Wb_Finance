from pydantic import BaseModel


class StoreItem(BaseModel):
    owner_user_id: str
    owner_email: str
    access: str  # owner | granted


class StoresListResponse(BaseModel):
    stores: list[StoreItem]


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

