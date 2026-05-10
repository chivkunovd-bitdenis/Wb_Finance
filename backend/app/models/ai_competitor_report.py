from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class AiCompetitorComparisonReport(Base):
    __tablename__ = "ai_competitor_comparison_reports"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    report_date = Column(Date, nullable=False, index=True)
    period = Column(String(16), nullable=False, default="unknown")  # week|month|quarter|unknown
    source = Column(String(32), nullable=False, default="manual")  # manual|playwright

    raw_payload = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "report_date",
            "period",
            name="uq_ai_competitor_report_user_date_period",
        ),
    )

