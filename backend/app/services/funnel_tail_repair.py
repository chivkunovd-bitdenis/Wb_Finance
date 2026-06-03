"""Rolling 7-day funnel repair: which calendar days still need a WB fetch."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.funnel_daily import FunnelDaily


def funnel_rolling_window(*, through: date | None = None) -> tuple[date, date]:
    end = through if through is not None else date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end


def funnel_days_needing_repair(
    db: Session,
    user_id: str,
    *,
    start: date,
    through: date,
) -> list[date]:
    """
    Days in [start..through] that need another products-API fetch:
    - no rows in funnel_daily, or
    - rows exist but order_count>0 while order_sum is zero (hollow metrics).
    """
    window_days = [start + timedelta(days=i) for i in range((through - start).days + 1)]

    present_dates = {
        d
        for (d,) in (
            db.query(FunnelDaily.date)
            .filter(
                FunnelDaily.user_id == user_id,
                FunnelDaily.date >= start,
                FunnelDaily.date <= through,
            )
            .distinct()
            .all()
        )
    }

    hollow_dates = {
        d
        for (d,) in (
            db.query(FunnelDaily.date)
            .filter(
                FunnelDaily.user_id == user_id,
                FunnelDaily.date >= start,
                FunnelDaily.date <= through,
            )
            .group_by(FunnelDaily.date)
            .having(func.coalesce(func.sum(FunnelDaily.order_count), 0) > 0)
            .having(func.coalesce(func.sum(FunnelDaily.order_sum), 0) == 0)
            .all()
        )
    }

    return [d for d in window_days if d not in present_dates or d in hollow_dates]
