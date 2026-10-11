from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import encrypt_text
from app.models.article import Article
from app.models.ozon import OzonAccrualSnapshot, OzonConnection, OzonProductCost, OzonSyncDay
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User
from app.services.marketplace_finance_service import (
    MarketplaceFinanceError,
    _dedupe_accruals,
    build_overview,
    enqueue_sync,
    run_sync_run,
    set_product_cost,
)


def _user(email: str, *, wb_api_key: str | None = None) -> User:
    return User(
        id=str(uuid4()),
        email=email,
        password_hash="hash",
        wb_api_key=wb_api_key,
        is_active=True,
    )


def _connection(user_id: str, client_id: str = "client-1", api_key: str = "secret-api-key") -> OzonConnection:
    return OzonConnection(
        user_id=user_id,
        client_id_enc=encrypt_text(client_id),
        api_key_enc=encrypt_text(api_key),
        status="ready",
    )


def _day_values(**overrides):
    values = {
        "revenue": "100.00",
        "commission": "10.00",
        "logistics": "4.00",
        "penalties": "0.00",
        "storage": "0.00",
        "ads_spend": "0.00",
        "other_expenses": "1.00",
        "compensation": "0.00",
        "cogs": None,
        "tax": None,
        "operation_expenses": None,
        "margin": None,
        "marketplace_net": "85.00",
        "unallocated_amount": "1.00",
        "reconciliation_difference": "0.00",
    }
    values.update(overrides)
    return values


def test_ozon_overview_reads_only_saved_day_and_keeps_unknowns(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-ozon-read@example.com")
    day = date(2026, 10, 10)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(str(owner.id)),
            OzonSyncDay(
                user_id=str(owner.id),
                day=day,
                status="complete",
                last_attempt_status="succeeded",
                daily_data=_day_values(revenue="100.005"),
                products_data=[{"sku": "7001", "revenue": "100.00", "cogs": None}],
                warnings=[],
            ),
            OzonProductCost(user_id=str(owner.id), sku="7001", cost_price=Decimal("0.00")),
        ]
    )
    real_db_session.commit()

    # A GET is served from local snapshots and never constructs the HTTP client.
    result = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )

    assert result["coverage"] == {"complete": True, "missing_dates": []}
    assert result["daily"][0]["revenue"] == 100.01
    assert result["daily"][0]["cogs"] is None
    assert result["products"][0]["marketplace"] == "ozon"
    assert result["products"][0]["sku"] == "7001"
    assert result["products"][0]["cost_price"] == 0.0


def test_all_overview_marks_unconfigured_ozon_incomplete_but_retains_wb_snapshot(real_db_session):
    owner = _user("marketplace-all-partial@example.com", wb_api_key=None)
    day = date(2026, 10, 10)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            PnlDaily(
                user_id=str(owner.id),
                date=day,
                revenue=Decimal("250.00"),
                commission=Decimal("25.00"),
                logistics=Decimal("5.00"),
                penalties=0,
                storage=0,
                ads_spend=0,
                cogs=Decimal("100.00"),
                tax=Decimal("15.00"),
                operation_expenses=Decimal("3.00"),
                margin=Decimal("102.00"),
            ),
        ]
    )
    real_db_session.commit()

    result = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="all",
        date_from=day,
        date_to=day,
    )

    assert result["coverage"]["complete"] is False
    assert result["coverage"]["missing_dates"] == [day.isoformat()]
    assert result["daily"][0]["revenue"] is None
    assert any("Ozon не подключён" in warning for warning in result["warnings"])


def test_all_overview_combines_both_sources_once_and_keeps_products_separate(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-all-complete@example.com", wb_api_key=None)
    day = date(2026, 10, 10)
    owner_id = str(owner.id)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(owner_id),
            PnlDaily(
                user_id=owner_id,
                date=day,
                revenue=Decimal("250.00"),
                commission=Decimal("25.00"),
                logistics=Decimal("5.00"),
                penalties=0,
                storage=0,
                ads_spend=0,
                cogs=Decimal("100.00"),
                tax=Decimal("15.00"),
                operation_expenses=Decimal("3.00"),
                margin=Decimal("102.00"),
            ),
            Article(user_id=owner_id, nm_id=7001, vendor_code="SAME-OFFER", name="WB item", cost_price=10),
            SkuDaily(user_id=owner_id, date=day, nm_id=7001, revenue=250, cogs=100, margin=102),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                last_attempt_status="succeeded",
                daily_data=_day_values(revenue="100.00", commission="10.00", logistics="4.00", cogs="30.00", tax="6.00", margin="50.00"),
                products_data=[{"sku": "7001", "revenue": "100.00", "cogs": "30.00"}],
                warnings=[],
            ),
            OzonProductCost(user_id=owner_id, sku="7001", cost_price=30),
        ]
    )
    real_db_session.commit()

    result = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="all",
        date_from=day,
        date_to=day,
    )

    assert result["coverage"]["complete"] is True
    assert result["daily"][0]["revenue"] == 350.0
    assert result["daily"][0]["commission"] == 35.0
    assert result["daily"][0]["operation_expenses"] == 3.0
    assert result["daily"][0]["margin"] == 152.0
    assert {product["marketplace"] for product in result["products"]} == {"wb", "ozon"}
    assert {product["sku"] for product in result["products"]} == {"7001"}


def test_repeated_accrual_id_with_changed_content_is_rejected():
    day = date(2026, 10, 10)
    first = {"accrual_id": "a-1", "date": day.isoformat(), "total_amount": {"amount": "5", "currency": "RUB"}}
    changed = {"accrual_id": "a-1", "date": day.isoformat(), "total_amount": {"amount": "7", "currency": "RUB"}}
    with pytest.raises(MarketplaceFinanceError):
        _dedupe_accruals([first, changed], day)


def test_cost_update_recomputes_saved_ledger_without_network(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-cost-recompute@example.com")
    day = date(2026, 10, 9)
    owner_id = str(owner.id)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(owner_id),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                source_count=1,
                daily_data=_day_values(),
                products_data=[{"sku": "7010", "revenue": "100.00", "cogs": None}],
                accrual_types=[{"id": 1, "name": "sale"}],
            ),
            OzonAccrualSnapshot(
                user_id=owner_id,
                accrual_id="recompute-source-1",
                accrual_date=day,
                category="POSTING",
                currency="RUB",
                raw_payload={"accrual_id": "recompute-source-1", "date": day.isoformat(), "sku": "7010"},
            ),
        ]
    )
    real_db_session.commit()

    seen_costs = []

    def fake_normalize(accruals, accrual_types, costs=None):
        seen_costs.append(costs)
        return {
            "daily": {day.isoformat(): _day_values(cogs=None)},
            "products": {
                "7010": {
                    "sku": "7010",
                    "daily": [_day_values(cogs=None)],
                }
            },
            "warnings": [],
        }

    monkeypatch.setattr("app.services.ozon_finance.normalize_accruals", fake_normalize)
    set_product_cost(db=real_db_session, user_id=owner_id, sku="7010", cost_price=Decimal("12.34"))
    real_db_session.refresh(real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one())
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.daily_data["cogs"] is None
    assert seen_costs == [{"7010": Decimal("12.34")}]
    assert state.accrual_types == [{"id": 1, "name": "sale"}]


def test_sync_persists_raw_accrual_types_normalized_day_and_catalogue(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-sync-snapshot@example.com")
    day = date(2026, 10, 8)
    owner_id = str(owner.id)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add(_connection(owner_id))
    real_db_session.commit()
    run = enqueue_sync(db=real_db_session, user_id=owner_id, date_from=day, date_to=day)
    raw = {
        "accrual_id": "source-1",
        "date": day.isoformat(),
        "accrued_category": "POSTING",
        "total_amount": {"amount": "85.00", "currency": "RUB"},
        "posting": {"products": [{"sku": "7001"}]},
    }
    accrual_types = [{"id": 41, "name": "source fee"}]

    class FakeClient:
        def __init__(self, client_id, api_key):
            assert client_id == "client-1"
            assert api_key == "secret-api-key"

        def fetch_accrual_types(self):
            return accrual_types

        def fetch_accruals_day(self, requested_day):
            assert requested_day == day
            return [raw]

        def fetch_products(self, skus):
            assert skus == ["7001"]
            return [{"sku": "7001", "offer_id": "offer-7", "name": "Catalog item"}]

        def close(self):
            pass

    def fake_normalize(accruals, types, costs=None):
        assert accruals == [raw]
        assert types == accrual_types
        return {
            "daily": [{"date": day.isoformat(), **_day_values()}],
            "products": [{"sku": "7001", "daily": [{"date": day.isoformat(), **_day_values()}]}],
            "warnings": [],
        }

    monkeypatch.setattr("app.services.ozon_client.OzonClient", FakeClient)
    monkeypatch.setattr("app.services.ozon_finance.normalize_accruals", fake_normalize)

    outcome = run_sync_run(db=real_db_session, run_id=str(run.id))

    assert outcome["status"] == "complete"
    snapshot = real_db_session.query(OzonAccrualSnapshot).filter_by(user_id=owner_id).one()
    assert snapshot.raw_payload == raw
    assert snapshot.category == "POSTING"
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.status == "complete"
    assert state.accrual_types == accrual_types
    assert state.daily_data["marketplace_net"] == "85.00"
    from app.models.ozon import OzonProduct

    product = real_db_session.query(OzonProduct).filter_by(user_id=owner_id, sku="7001").one()
    assert (product.offer_id, product.name) == ("offer-7", "Catalog item")

    # A second complete fetch replaces that day's snapshot; it does not append
    # another copy of the same accrual or double the normalized total.
    second_run = enqueue_sync(db=real_db_session, user_id=owner_id, date_from=day, date_to=day)
    second_outcome = run_sync_run(db=real_db_session, run_id=str(second_run.id))
    assert second_outcome["status"] == "complete"
    assert real_db_session.query(OzonAccrualSnapshot).filter_by(user_id=owner_id).count() == 1
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.source_count == 1
    assert state.daily_data["marketplace_net"] == "85.00"


def test_failed_refresh_preserves_last_complete_day_and_raw_snapshot(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-sync-preserve@example.com")
    day = date(2026, 10, 7)
    owner_id = str(owner.id)
    previous_raw = {
        "accrual_id": "source-old",
        "date": day.isoformat(),
        "total_amount": {"amount": "10.00", "currency": "RUB"},
    }
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(owner_id),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                last_attempt_status="succeeded",
                source_count=1,
                daily_data=_day_values(revenue="10.00"),
                products_data=[],
                accrual_types=[{"id": 1, "name": "stored type"}],
                warnings=[],
            ),
            OzonAccrualSnapshot(
                user_id=owner_id,
                accrual_id="source-old",
                accrual_date=day,
                currency="RUB",
                raw_payload=previous_raw,
            ),
        ]
    )
    real_db_session.commit()
    run = enqueue_sync(db=real_db_session, user_id=owner_id, date_from=day, date_to=day)

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_accrual_types(self):
            return [{"id": 2, "name": "new type"}]

        def fetch_accruals_day(self, requested_day):
            raise RuntimeError("provider failure")

        def close(self):
            pass

    monkeypatch.setattr("app.services.ozon_client.OzonClient", FailingClient)

    outcome = run_sync_run(db=real_db_session, run_id=str(run.id))

    assert outcome["status"] == "failed"
    snapshot = real_db_session.query(OzonAccrualSnapshot).filter_by(user_id=owner_id).one()
    assert snapshot.raw_payload == previous_raw
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.status == "complete"
    assert state.daily_data["revenue"] == "10.00"
    assert state.last_attempt_status == "failed"


@pytest.mark.parametrize(
    ("email", "source_date", "currency"),
    [
        ("marketplace-sync-wrong-date@example.com", "2026-10-06", "RUB"),
        ("marketplace-sync-wrong-currency@example.com", "2026-10-07", "USD"),
    ],
)
def test_bad_date_or_currency_cannot_replace_saved_day(real_db_session, monkeypatch, email, source_date, currency):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user(email)
    day = date(2026, 10, 7)
    owner_id = str(owner.id)
    previous_raw = {
        "accrual_id": f"old-{email}",
        "date": day.isoformat(),
        "total_amount": {"amount": "10.00", "currency": "RUB"},
    }
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(owner_id),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                last_attempt_status="succeeded",
                source_count=1,
                daily_data=_day_values(revenue="10.00"),
                products_data=[],
                accrual_types=[{"id": 1}],
                warnings=[],
            ),
            OzonAccrualSnapshot(
                user_id=owner_id,
                accrual_id=f"old-{email}",
                accrual_date=day,
                currency="RUB",
                raw_payload=previous_raw,
            ),
        ]
    )
    real_db_session.commit()
    run = enqueue_sync(db=real_db_session, user_id=owner_id, date_from=day, date_to=day)

    class InvalidDataClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_accrual_types(self):
            return [{"id": 2}]

        def fetch_accruals_day(self, requested_day):
            return [
                {
                    "accrual_id": f"new-{email}",
                    "date": source_date,
                    "total_amount": {"amount": "9.00", "currency": currency},
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr("app.services.ozon_client.OzonClient", InvalidDataClient)

    outcome = run_sync_run(db=real_db_session, run_id=str(run.id))

    assert outcome["status"] == "failed"
    snapshot = real_db_session.query(OzonAccrualSnapshot).filter_by(user_id=owner_id).one()
    assert snapshot.raw_payload == previous_raw
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.status == "complete"
    assert state.daily_data["revenue"] == "10.00"
    assert state.last_attempt_status == "failed"


def test_genuine_empty_day_has_known_zero_ledger_and_unknown_external_metrics(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-sync-empty@example.com")
    day = date(2026, 10, 6)
    owner_id = str(owner.id)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add(_connection(owner_id))
    real_db_session.commit()
    run = enqueue_sync(db=real_db_session, user_id=owner_id, date_from=day, date_to=day)

    class EmptyClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_accrual_types(self):
            return []

        def fetch_accruals_day(self, requested_day):
            return []

        def close(self):
            pass

    monkeypatch.setattr("app.services.ozon_client.OzonClient", EmptyClient)
    monkeypatch.setattr(
        "app.services.ozon_finance.normalize_accruals",
        lambda accruals, accrual_types, costs=None: {"daily": [], "products": [], "warnings": []},
    )

    outcome = run_sync_run(db=real_db_session, run_id=str(run.id))

    assert outcome["status"] == "complete"
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.status == "empty"
    assert state.accrual_types == []
    assert state.daily_data["revenue"] == "0"
    assert state.daily_data["commission"] == "0"
    for field in ("ads_spend", "tax", "operation_expenses", "margin"):
        assert state.daily_data[field] is None
