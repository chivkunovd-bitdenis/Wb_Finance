from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class AiHypothesisDailyLog(Base):
    __tablename__ = "ai_hypothesis_daily_log"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    hypothesis_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ai_hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day = Column(Date, nullable=False)

    happened = Column(Text, nullable=True)
    changed = Column(Text, nullable=True)
    unchanged = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("hypothesis_id", "day", name="uq_ai_hypothesis_daily_log_hyp_day"),
    )

