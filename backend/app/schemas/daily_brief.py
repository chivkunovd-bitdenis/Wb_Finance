"""Pydantic-схемы для эндпоинтов ежедневной сводки."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DailyBriefResponse(BaseModel):
    date_for: str
    status: str          # pending | generating | ready | error
    text: Optional[str]
    error_message: Optional[str]
    generated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class DailyBriefTriggerResponse(BaseModel):
    status: str
    message: str
    date_for: str
