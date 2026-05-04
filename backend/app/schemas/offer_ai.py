from __future__ import annotations

from pydantic import BaseModel, Field


class OfferSourceItem(BaseModel):
    chunk_id: int
    score: float
    text: str


class OfferAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class OfferAskResponse(BaseModel):
    answer: str
    sources: list[OfferSourceItem]
    active_version: str | None


class OfferStatusResponse(BaseModel):
    status: str
    active_version: str | None
    indexed_at: str | None
    error_message: str | None


class OfferUploadResponse(BaseModel):
    status: str
    next_version: str

