"""PG-C.1: ответ модели для серии контентных фото WB."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContentSeriesResult(BaseModel):
    series_prompts: list[str] = Field(..., min_length=7, max_length=7)
