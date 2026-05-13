from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreateBody(BaseModel):
    """Тело POST /internal/v1/runs (монолит передаёт связь и опциональные метаданные)."""

    monolith_job_id: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] | None = None


class RunCreateResponse(BaseModel):
    id: str
    status: str


class PipelineStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    step_key: str
    ordinal: int
    status: str
    error_message: str | None
    meta_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class PipelineAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    step_id: str | None
    kind: str
    storage_rel_path: str
    mime_type: str | None
    sha256_hex: str | None
    meta_json: dict[str, Any] | None
    created_at: datetime


class RunDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    monolith_job_id: str | None
    payload: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    steps: list[PipelineStepOut]
    assets: list[PipelineAssetOut]
