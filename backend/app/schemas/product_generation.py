from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class SizeRow(BaseModel):
    tech_size: str = Field(..., min_length=1, max_length=255)
    wb_size: str = Field(..., min_length=1, max_length=255)


class ProductGenerationJobCreate(BaseModel):
    vendor_code: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=1000)
    brand: str | None = Field(default=None, max_length=500)
    wb_subject_id: int | None = Field(
        default=None,
        ge=1,
        description="ID предмета WB (категория); опционально — черновик без него валиден (PG-2.4).",
    )
    description_user: str | None = None
    seo_description: str | None = None
    price_kopeks: int | None = Field(default=None, ge=0)
    dimensions_length: Decimal | None = None
    dimensions_width: Decimal | None = None
    dimensions_height: Decimal | None = None
    weight_brutto: Decimal | None = None
    sizes: list[SizeRow] | None = None
    reference_paths_json: list[Any] | dict[str, Any] | None = None


class ProductGenerationJobUpdate(BaseModel):
    vendor_code: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=1000)
    brand: str | None = Field(default=None, max_length=500)
    wb_subject_id: int | None = Field(
        default=None,
        ge=1,
        description="ID предмета WB; null сбрасывает значение при PATCH.",
    )
    description_user: str | None = None
    seo_description: str | None = None
    price_kopeks: int | None = Field(default=None, ge=0)
    dimensions_length: Decimal | None = None
    dimensions_width: Decimal | None = None
    dimensions_height: Decimal | None = None
    weight_brutto: Decimal | None = None
    sizes: list[SizeRow] | None = None
    reference_paths_json: list[Any] | dict[str, Any] | None = None
    selected_main_asset_id: str | None = Field(default=None, max_length=64)
    selected_series_asset_ids: list[str] | None = None
    status: str | None = Field(
        default=None,
        description="draft|in_progress|error|ready_to_publish|published",
    )


class ProductGenerationJobOut(BaseModel):
    id: str
    user_id: str
    status: str
    pipeline_run_id: str | None
    vendor_code: str | None
    title: str | None
    brand: str | None
    wb_subject_id: int | None
    description_user: str | None
    seo_description: str | None
    price_kopeks: int | None
    dimensions_length: Decimal | None
    dimensions_width: Decimal | None
    dimensions_height: Decimal | None
    weight_brutto: Decimal | None
    sizes_json: list[dict[str, Any]] | None
    reference_paths_json: list[Any] | dict[str, Any] | None
    selected_main_asset_id: str | None
    selected_series_asset_ids: list[str] | None
    wb_publish_error: str | None
    wb_response_json: dict[str, Any] | list[Any] | None
    created_at: datetime
    updated_at: datetime
    image_pipeline: dict[str, Any] | None = Field(
        default=None,
        description="Снимок GET image-сервиса /internal/v1/runs/{id} (PG-3.4), только для удалённого run.",
    )

    model_config = {"from_attributes": True}


class ProductGenerationJobListResponse(BaseModel):
    items: list[ProductGenerationJobOut]
