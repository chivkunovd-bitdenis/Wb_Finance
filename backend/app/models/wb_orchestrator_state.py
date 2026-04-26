from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WbOrchestratorState(Base):
    """
    Single orchestrator state per user/seller.

    Intents are aggregated here (no fan-out tasks per period).
    """

    __tablename__ = "wb_orchestrator_state"

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    status: Mapped[str] = mapped_column(String, nullable=False, server_default="idle")
    # ISO timestamp string (UTC) for simplicity in JSON + UI.
    cooldown_until: Mapped[str | None] = mapped_column(String, nullable=True)
    last_step: Mapped[str | None] = mapped_column(String, nullable=True)

    # Intents:
    # - high: recent + repair
    # - low: backfills
    intents: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

