from datetime import date, timedelta


def test_compute_missing_tail_range_returns_yesterday_when_only_yesterday_missing(real_db_session):
    from app.models.user import User
    from app.models.pnl_daily import PnlDaily
    from app.services.finance_missing_tail import compute_missing_tail_range

    u = User(email="miss1@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()
    user_id = str(u.id)
    through = date.today() - timedelta(days=1)
    day_before = through - timedelta(days=1)

    real_db_session.add(
        PnlDaily(
            user_id=user_id,
            date=day_before,
            revenue=1,
            commission=0,
            logistics=0,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=0,
            tax=0,
            margin=1,
        )
    )
    real_db_session.commit()

    rng = compute_missing_tail_range(real_db_session, user_id=user_id, through=through, lookback_days=10)
    assert rng is not None
    assert rng.date_from == through
    assert rng.date_to == through


def test_compute_missing_tail_range_none_when_through_present(real_db_session):
    from app.models.user import User
    from app.models.pnl_daily import PnlDaily
    from app.services.finance_missing_tail import compute_missing_tail_range

    u = User(email="miss2@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()
    user_id = str(u.id)
    through = date.today() - timedelta(days=1)

    real_db_session.add(
        PnlDaily(
            user_id=user_id,
            date=through,
            revenue=1,
            commission=0,
            logistics=0,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=0,
            tax=0,
            margin=1,
        )
    )
    real_db_session.commit()

    assert compute_missing_tail_range(real_db_session, user_id=user_id, through=through, lookback_days=10) is None


def test_compute_missing_ranges_in_window_finds_middle_hole(real_db_session):
    from app.models.user import User
    from app.models.pnl_daily import PnlDaily
    from app.services.finance_missing_tail import compute_missing_ranges_in_window

    u = User(email="holes@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()
    user_id = str(u.id)

    base = date.today() - timedelta(days=10)
    # present: base, base+1, base+4; hole: base+2..base+3
    real_db_session.add_all(
        [
            PnlDaily(user_id=user_id, date=base, revenue=1, commission=0, logistics=0, penalties=0, storage=0, ads_spend=0, cogs=0, tax=0, margin=1, operation_expenses=0),
            PnlDaily(user_id=user_id, date=base + timedelta(days=1), revenue=1, commission=0, logistics=0, penalties=0, storage=0, ads_spend=0, cogs=0, tax=0, margin=1, operation_expenses=0),
            PnlDaily(user_id=user_id, date=base + timedelta(days=4), revenue=1, commission=0, logistics=0, penalties=0, storage=0, ads_spend=0, cogs=0, tax=0, margin=1, operation_expenses=0),
        ]
    )
    real_db_session.commit()

    ranges = compute_missing_ranges_in_window(
        real_db_session,
        user_id=user_id,
        date_from=base,
        date_to=base + timedelta(days=4),
    )
    assert ranges
    # should include the middle hole
    assert any(r.date_from == base + timedelta(days=2) and r.date_to == base + timedelta(days=3) for r in ranges)

