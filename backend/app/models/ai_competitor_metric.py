from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class AiCompetitorMetric(Base):
    __tablename__ = "ai_competitor_metrics"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    report_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ai_competitor_comparison_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    nm_id = Column(Integer, nullable=False, index=True)

    # Canonical metric codes for analytics rules (AI-MVP3):
    # ctr, traffic, funnel_cart, funnel_order
    metric_code = Column(String(32), nullable=False, index=True)

    our_value = Column(Numeric(18, 6), nullable=True)
    competitor_median_value = Column(Numeric(18, 6), nullable=True)

    unit = Column(String(16), nullable=True)  # %, rub, abs, etc.
    extra = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("report_id", "nm_id", "metric_code", name="uq_ai_comp_metric_report_nm_code"),
    )

