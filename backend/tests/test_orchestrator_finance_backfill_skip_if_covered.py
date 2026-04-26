from __future__ import annotations

from datetime import date as real_date
from datetime import timedelta
from unittest.mock import patch

from app.models.pnl_daily import PnlDaily
from app.models.user import User
from app.models.wb_orchestrator_state import WbOrchestratorState
from celery_app.tasks import wb_orchestrator_tick


class _FixedDate(real_date):
    _fixed_today = real_date(2026, 4, 8)

    @classmethod
    def today(cls) -> real_date:  # type: ignore[override]
        return cls._fixed_today


def test_orchestrator_finance_backfill_skips_wb_when_pnl_daily_covered(real_db_session):
    """
    Регрессия: архивный backfill не должен повторно дергать WB, если витрина pnl_daily
    уже покрывает период чанка (значит данные за этот период уже были загружены).
    """
    u = User(email="orch-skip@example.com", password_hash="$2b$12$fake", wb_api_key="k", is_active=True)
    real_db_session.add(u)
    real_db_session.commit()
    real_db_session.refresh(u)
    user_id = str(u.id)

    # Для фиксированной "сегодня" чанк будет 2026-04-01..2026-04-07
    today = _FixedDate.today()
    yesterday = today - timedelta(days=1)
    df_d = real_date(yesterday.year, yesterday.month, 1)
    dt_d = yesterday

    for i in range((dt_d - df_d).days + 1):
        real_db_session.add(PnlDaily(user_id=user_id, date=df_d + timedelta(days=i)))
    real_db_session.commit()

    st = WbOrchestratorState(
        user_id=user_id,
        status="idle",
        intents={"low": {"finance_backfill_year": 2026}},
        cooldown_until=None,
        last_step=None,
    )
    real_db_session.add(st)
    real_db_session.commit()

    with (
        patch("celery_app.tasks.date", _FixedDate),
        patch("celery_app.tasks.SessionLocal", return_value=real_db_session),
        patch("celery_app.tasks.sync_sales") as mock_sales,
        patch("celery_app.tasks.sync_ads") as mock_ads,
        patch("celery_app.tasks.recalculate_pnl.delay") as mock_pnl_delay,
        patch("celery_app.tasks.recalculate_sku_daily.delay") as mock_sku_delay,
        patch.object(wb_orchestrator_tick, "apply_async", return_value=None),
    ):
        out = wb_orchestrator_tick(user_id)

    assert out.get("ok") is True, out
    mock_sales.assert_not_called()
    mock_ads.assert_not_called()
    mock_pnl_delay.assert_not_called()
    mock_sku_delay.assert_not_called()

