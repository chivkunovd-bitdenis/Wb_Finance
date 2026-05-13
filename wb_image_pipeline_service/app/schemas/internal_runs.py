from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MonolithImageRunPayload(BaseModel):
    """
    JSON `payload` от монолита для фазы IMAGE (PG-A.2).

    Обязательны только идентификаторы референсов, уже загруженных в монолит.
    Поля карточки WB опциональны и могут быть null — воркер не должен от них зависеть
    на этапе структуризации/картинок.
    """

    model_config = ConfigDict(extra="allow")

    reference_asset_ids: list[str] = Field(..., min_length=1)
    description_user: str | None = None
    title: str | None = None
    vendor_code: str | None = None
    brand: str | None = None
    wb_subject_id: int | None = Field(default=None, ge=1)
    seo_description: str | None = None
    price_kopeks: int | None = Field(default=None, ge=0)
    dimensions_length: str | None = None
    dimensions_width: str | None = None
    dimensions_height: str | None = None
    weight_brutto: str | None = None
    sizes_json: Any | None = None

    @field_validator("reference_asset_ids", mode="before")
    @classmethod
    def _reference_ids_non_empty_strings(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("reference_asset_ids must be a list")
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if not s:
                raise ValueError("reference_asset_ids must contain only non-empty strings")
            out.append(s)
        if not out:
            raise ValueError("reference_asset_ids must not be empty")
        return out


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
