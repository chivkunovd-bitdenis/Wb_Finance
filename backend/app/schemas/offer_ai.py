from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class OfferSourceItem(BaseModel):
    chunk_id: int
    score: float
    text: str
    metadata: dict | None = None


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


class OfferChatStartRequest(BaseModel):
    chat_id: UUID


class OfferChatStartResponse(BaseModel):
    chat_id: UUID
    active_version: str


class OfferChatAskRequest(BaseModel):
    chat_id: UUID
    message: str = Field(min_length=1, max_length=50_000)


class OfferChatAskResponse(BaseModel):
    chat_id: UUID
    answer: str
    sources: list[OfferSourceItem]
    active_version: str
    need_clarification: bool = False


class OfferChatHistoryItem(BaseModel):
    role: str
    content: str
    created_at: str


class OfferChatHistoryResponse(BaseModel):
    chat_id: UUID
    active_version: str
    messages: list[OfferChatHistoryItem]

