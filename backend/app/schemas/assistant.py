from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class AssistantMessageItem(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: str
    kind: str = "chat"
    sources: list[dict] | None = None


class AssistantHistoryResponse(BaseModel):
    messages: list[AssistantMessageItem]


class AssistantAskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class AssistantCfoAnalysisRequest(BaseModel):
    date: str | None = None
