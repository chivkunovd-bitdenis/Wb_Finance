from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MonthlyPlan(Base):
    __tablename__ = "monthly_plan"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)

    # First day of month (YYYY-MM-01).
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Metric key (e.g. "revenue", "commission_pct").
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # Plan value: numeric fields store currency amount; percent fields store percent number (e.g. 15 for 15%).
    value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "month", "metric_key", name="uq_monthly_plan_user_month_metric"),
    )

