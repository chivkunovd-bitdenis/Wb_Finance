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
    # One logical import (manual or Playwright) — rows accumulate across re-imports for same report_date/period.
    import_batch_id = Column(UUID(as_uuid=False), nullable=False, index=True)

    nm_id = Column(Integer, nullable=False, index=True)

    # Canonical metric codes for analytics rules (AI-MVP3):
    # traffic — абсолют (показы), агрегат — среднее; ctr / funnel_* — п.п. (ctr: доля 0–1 в Excel → ×100), агрегат — медиана.
    metric_code = Column(String(32), nullable=False, index=True)

    our_value = Column(Numeric(18, 6), nullable=True)
    competitor_median_value = Column(Numeric(18, 6), nullable=True)

    unit = Column(String(16), nullable=True)  # %, rub, abs, etc.
    extra = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "nm_id",
            "metric_code",
            "import_batch_id",
            name="uq_ai_comp_metric_report_nm_code_batch",
        ),
    )

