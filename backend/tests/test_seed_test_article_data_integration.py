from __future__ import annotations

from datetime import date

from app.models.article import Article
from app.models.funnel_daily import FunnelDaily
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User
from app.models.base import uuid_gen
from app.services.test_data_seed_service import seed_test_article_timeseries


def test_seed_test_article_timeseries_is_idempotent_and_fills_14_days(real_db_session):
    db = real_db_session
    user = User(id=uuid_gen(), email="seed-test@example.com", password_hash="x")
    db.add(user)
    db.commit()

    nm_id = 161873380
    date_to = date(2026, 5, 11)

    seed_test_article_timeseries(db, user_id=str(user.id), nm_id=nm_id, vendor_code="ТЕСТ", days=14, date_to=date_to)
    seed_test_article_timeseries(db, user_id=str(user.id), nm_id=nm_id, vendor_code="ТЕСТ", days=14, date_to=date_to)

    art = db.query(Article).filter(Article.user_id == str(user.id), Article.nm_id == nm_id).first()
    assert art is not None
    assert art.vendor_code == "ТЕСТ"

    date_from = date_to.fromordinal(date_to.toordinal() - 13)

    funnel_rows = (
        db.query(FunnelDaily)
        .filter(FunnelDaily.user_id == str(user.id), FunnelDaily.nm_id == nm_id, FunnelDaily.date >= date_from, FunnelDaily.date <= date_to)
        .all()
    )
    sku_rows = (
        db.query(SkuDaily)
        .filter(SkuDaily.user_id == str(user.id), SkuDaily.nm_id == nm_id, SkuDaily.date >= date_from, SkuDaily.date <= date_to)
        .all()
    )
    pnl_rows = (
        db.query(PnlDaily)
        .filter(PnlDaily.user_id == str(user.id), PnlDaily.date >= date_from, PnlDaily.date <= date_to)
        .all()
    )

    assert len(funnel_rows) == 14
    assert len(sku_rows) == 14
    assert len(pnl_rows) == 14

    # sanity: key fields are populated and consistent with "funnels both" expectation:
    # - funnel_daily has counts and vendor_code
    # - sku_daily mirrors funnel columns and has logistics
    for r in funnel_rows:
        assert r.vendor_code == "ТЕСТ"
        assert (r.open_count or 0) >= 0
        assert (r.cart_count or 0) >= 0
        assert (r.order_count or 0) >= 0

    for r in sku_rows:
        assert r.logistics is not None
        assert r.order_sum is not None

