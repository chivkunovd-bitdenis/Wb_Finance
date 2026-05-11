from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
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

    valid_until = Column(Date, nullable=True)
    status = Column(String(16), nullable=False, default="ready")  # ready|stale|running|error
    cost_or_limit_spent = Column(Boolean, nullable=False, default=False)
    last_error = Column(Text, nullable=True)

    raw_payload = Column(JSONB, nullable=True)
    # Points to import_batch_id on metrics used for analytics and default GET detail view.
    latest_import_batch_id = Column(UUID(as_uuid=False), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "report_date",
            "period",
            name="uq_ai_competitor_report_user_date_period",
        ),
        CheckConstraint(
            "status in ('ready','stale','running','error')",
            name="ck_ai_competitor_reports_status",
        ),
    )

