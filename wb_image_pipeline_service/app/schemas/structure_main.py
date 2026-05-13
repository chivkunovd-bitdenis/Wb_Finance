"""PG-B.2: ответ модели структуризации (SEO + 4 промпта главного кадра)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StructureMainResult(BaseModel):
    seo_title: str = Field(..., min_length=1, max_length=500)
    seo_description: str = Field(..., min_length=1, max_length=12000)
    main_prompts: list[str] = Field(..., min_length=4, max_length=4)
