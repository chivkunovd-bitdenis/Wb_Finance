"""Automatic Ozon synchronisation and WB-shaped marketplace rows.

Provider calls are replaced by fakes; the database is the transactional real_db_session fixture.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.crypto import encrypt_text
from app.db import get_db
from app.main import app
from app.models.ozon import OzonConnection, OzonProduct, OzonProductCost, OzonSyncDay, OzonSyncRun
from app.models.user import User
from app.services.marketplace_finance_service import (
    WB_SHAPED_PNL_FIELDS,
    backfill_window,
    build_marketplace_article_rows,
    build_marketplace_pnl_rows,
    build_marketplace_sku_rows,
    chain_backfill_after_run,
    connection_status,
    enqueue_sync,
    next_backfill_run,
    plan_daily_refresh_for_ready_connections,
    run_sync_run,
)
from tests.test_marketplace_finance_api import _install_db_override, _token

TODAY = date(2026, 2, 10)  # through 9 February: the backfill is Jan 1..Jan 31 then Feb 1..Feb 9


class EmptyProvider:
    """Every day has no accruals; the sync still runs the complete per-day path."""

    def __init__(self, client_id, api_key):
        assert client_id == "client-1"

    def fetch_accrual_types(self):
        return []

    def fetch_accruals_day(self, day):
        return []

    def fetch_products(self, skus):
        return []

    def close(self):
        pass


class FailingProvider(EmptyProvider):
    def fetch_accrual_types(self):
        raise RuntimeError("provider unavailable")


class FixedDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def dispatched(monkeypatch):
    """Record which run IDs would be handed to Celery; nothing executes eagerly."""
    queue: list[str] = []
    monkeypatch.setattr("celery_app.ozon_tasks.ozon_sync_run.delay", lambda run_id: queue.append(run_id))
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return queue


def _owner(session, email: str, *, status: str = "ready", tax_rate: str | None = "0.06") -> str:
    owner = User(
        id=str(uuid4()),
        email=email,
        password_hash="hash",
        is_active=True,
        tax_rate=Decimal(tax_rate) if tax_rate is not None else None,
    )
    session.add(owner)
    session.commit()
    owner_id = str(owner.id)
    session.add(
        OzonConnection(
            user_id=owner_id,
            client_id_enc=encrypt_text("client-1"),
            api_key_enc=encrypt_text("secret-api-key"),
            status=status,
        )
    )
    session.commit()
    return owner_id


def _finished_backfill(session, owner_id: str, date_from: date, date_to: date) -> None:
    session.add(
        OzonSyncRun(
            user_id=owner_id,
            date_from=date_from,
            date_to=date_to,
            status="complete",
            trigger="backfill",
            progress=(date_to - date_from).days + 1,
        )
    )
    session.commit()


def _day_values(**overrides):
    values = {
        "revenue": "100.00",
        "commission": "10.00",
        "logistics": "4.00",
        "penalties": "0.00",
        "storage": "0.00",
        "ads_spend": "0.00",
        "other_expenses": "0.00",
        "compensation": "0.00",
        "cogs": None,
        "tax": None,
        "operation_expenses": None,
        "margin": None,
        "marketplace_net": "86.00",
        "unallocated_amount": "0.00",
        "reconciliation_difference": "0.00",
    }
    values.update(overrides)
    return values


# ── Backfill window and chunk chaining ───────────────────────────────────────


def test_backfill_window_runs_from_new_year_through_yesterday():
    assert backfill_window(date(2026, 10, 25)) == (date(2026, 1, 1), date(2026, 10, 24))
    assert backfill_window(date(2026, 1, 1)) is None


def test_backfill_chain_queues_next_chunk_only_after_the_previous_run_finished(real_db_session, monkeypatch):
    monkeypatch.setattr("app.services.ozon_client.OzonClient", EmptyProvider)
    owner_id = _owner(real_db_session, "auto-sync-chain@example.com", status="configured")

    first = next_backfill_run(real_db_session, user_id=owner_id, today=TODAY)
    assert first is not None
    assert (first.trigger, first.date_from, first.date_to) == ("backfill", date(2026, 1, 1), date(2026, 1, 31))
    # A queued chunk holds the owner's single slot, so no second chunk is created yet.
    assert next_backfill_run(real_db_session, user_id=owner_id, today=TODAY) is None
    assert connection_status(db=real_db_session, user_id=owner_id, today=TODAY)["backfill"]["status"] == "running"

    assert run_sync_run(db=real_db_session, run_id=str(first.id))["status"] == "complete"
    second = chain_backfill_after_run(db=real_db_session, run_id=str(first.id), today=TODAY)
    assert second is not None
    assert (second.trigger, second.date_from, second.date_to) == ("backfill", date(2026, 2, 1), date(2026, 2, 9))

    assert run_sync_run(db=real_db_session, run_id=str(second.id))["status"] == "complete"
    assert chain_backfill_after_run(db=real_db_session, run_id=str(second.id), today=TODAY) is None
    backfill = connection_status(db=real_db_session, user_id=owner_id, today=TODAY)["backfill"]
    assert backfill == {
        "status": "complete",
        "date_from": "2026-01-01",
        "through_date": "2026-02-09",
        "last_completed_date": "2026-02-09",
        "error": None,
    }


def test_failed_backfill_chunk_is_retried_as_the_same_range(real_db_session, monkeypatch):
    monkeypatch.setattr("app.services.ozon_client.OzonClient", FailingProvider)
    owner_id = _owner(real_db_session, "auto-sync-retry@example.com", status="configured")
    first = next_backfill_run(real_db_session, user_id=owner_id, today=TODAY)
    assert run_sync_run(db=real_db_session, run_id=str(first.id))["status"] == "failed"

    backfill = connection_status(db=real_db_session, user_id=owner_id, today=TODAY)["backfill"]
    assert backfill["status"] == "failed"
    assert backfill["error"]

    retry = next_backfill_run(real_db_session, user_id=owner_id, today=TODAY)
    assert retry is not None
    assert (retry.date_from, retry.date_to) == (date(2026, 1, 1), date(2026, 1, 31))


def test_manual_and_daily_runs_never_chain_a_backfill(real_db_session, monkeypatch):
    monkeypatch.setattr("app.services.ozon_client.OzonClient", EmptyProvider)
    owner_id = _owner(real_db_session, "auto-sync-manual@example.com")
    manual = enqueue_sync(db=real_db_session, user_id=owner_id, date_from=date(2026, 2, 1), date_to=date(2026, 2, 2))
    assert manual.trigger == "manual"
    assert run_sync_run(db=real_db_session, run_id=str(manual.id))["status"] == "complete"
    assert chain_backfill_after_run(db=real_db_session, run_id=str(manual.id), today=TODAY) is None


def test_backfill_chunks_never_exceed_thirty_one_days(real_db_session, monkeypatch):
    monkeypatch.setattr("app.services.ozon_client.OzonClient", EmptyProvider)
    owner_id = _owner(real_db_session, "auto-sync-limit@example.com", status="configured")
    run = next_backfill_run(real_db_session, user_id=owner_id, today=date(2026, 10, 25))
    assert (run.date_to - run.date_from).days + 1 == 31


# ── Daily beat refresh ───────────────────────────────────────────────────────


def test_daily_refresh_covers_last_three_completed_days_for_ready_connections_only(real_db_session):
    ready_owner = _owner(real_db_session, "auto-sync-daily-ready@example.com", status="ready")
    _finished_backfill(real_db_session, ready_owner, date(2026, 1, 1), date(2026, 2, 9))
    error_owner = _owner(real_db_session, "auto-sync-daily-error@example.com", status="error")
    _finished_backfill(real_db_session, error_owner, date(2026, 1, 1), date(2026, 2, 9))

    queued = plan_daily_refresh_for_ready_connections(db=real_db_session, today=TODAY)

    assert len(queued) == 1
    run = real_db_session.query(OzonSyncRun).filter_by(id=queued[0]).one()
    assert run.user_id == ready_owner
    assert run.trigger == "daily"
    assert (run.date_from, run.date_to) == (date(2026, 2, 7), date(2026, 2, 9))


def test_daily_refresh_resumes_an_unfinished_backfill_instead_of_a_daily_run(real_db_session):
    owner_id = _owner(real_db_session, "auto-sync-daily-resume@example.com", status="ready")

    queued = plan_daily_refresh_for_ready_connections(db=real_db_session, today=TODAY)

    run = real_db_session.query(OzonSyncRun).filter_by(id=queued[0]).one()
    assert run.user_id == owner_id
    assert run.trigger == "backfill"
    assert (run.date_from, run.date_to) == (date(2026, 1, 1), date(2026, 1, 31))


# ── WB-shaped rows: same fields as /dashboard/pnl, /dashboard/sku, /dashboard/articles ──


def test_pnl_rows_use_wb_fields_and_keep_unknown_or_missing_days_null(real_db_session):
    owner_id = _owner(real_db_session, "auto-sync-pnl-shape@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    real_db_session.add(
        OzonSyncDay(
            user_id=owner_id,
            day=date(2026, 2, 3),
            status="complete",
            last_attempt_status="succeeded",
            daily_data=_day_values(),
            products_data=[],
            warnings=[],
        )
    )
    real_db_session.commit()

    rows = build_marketplace_pnl_rows(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=date(2026, 2, 3),
        date_to=date(2026, 2, 4),
    )

    assert [row["date"] for row in rows] == ["2026-02-03", "2026-02-04"]
    # The only addition to the WB fields is the revenue that carries a known SKU cost, used for the caption.
    assert all(set(row) == {"date", *WB_SHAPED_PNL_FIELDS, "known_cost_revenue"} for row in rows)
    known, missing = rows
    assert known["known_cost_revenue"] == 0.0  # the day has revenue but no SKU with a cost
    assert missing["known_cost_revenue"] is None
    assert known["revenue"] == 100.0
    assert known["commission"] == 10.0  # expenses keep the WB positive convention
    assert known["logistics"] == 4.0
    assert known["tax"] == 6.0  # revenue × the owner's WB tax rate
    assert known["operation_expenses"] == 0.0  # no expense entered for the day counts as zero, as in WB
    assert known["margin"] is None  # no SKU on this day has a known cost, so no margin is claimed
    assert all(missing[field] is None for field in WB_SHAPED_PNL_FIELDS)


def test_sku_rows_have_null_order_counts_and_text_keys(real_db_session):
    owner_id = _owner(real_db_session, "auto-sync-sku-shape@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    real_db_session.add(
        OzonSyncDay(
            user_id=owner_id,
            day=date(2026, 2, 3),
            status="complete",
            last_attempt_status="succeeded",
            daily_data=_day_values(),
            products_data=[
                {
                    "sku": "7001",
                    "revenue": "100.00",
                    "commission": "10.00",
                    "logistics": "4.00",
                    "penalties": "0.00",
                    "storage": "0.00",
                    "ads_spend": "0.00",
                    "cogs": None,
                    "marketplace_net": "86.00",
                    "profitability_complete": False,
                    "accrual_classification_complete": True,
                    "sku_allocation_complete": True,
                }
            ],
            warnings=[],
        )
    )
    real_db_session.commit()

    rows = build_marketplace_sku_rows(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=date(2026, 2, 3),
        date_to=date(2026, 2, 3),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["nm_id"] == "7001"
    assert row["marketplace"] == "ozon"
    assert row["revenue"] == 100.0
    assert row["cogs"] is None
    assert row["margin"] is None
    assert row["order_count"] is None and row["order_sum"] is None and row["open_count"] is None


def test_article_rows_keep_an_unset_cost_null_and_use_catalogue_names(real_db_session):
    owner_id = _owner(real_db_session, "auto-sync-articles-shape@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    real_db_session.add_all(
        [
            OzonProduct(user_id=owner_id, sku="7001", offer_id="offer-7", name="Catalog item"),
            OzonProduct(user_id=owner_id, sku="7002", offer_id="offer-8", name=None),
            OzonProductCost(user_id=owner_id, sku="7001", cost_price=Decimal("12.5")),
        ]
    )
    real_db_session.commit()

    rows = {row["nm_id"]: row for row in build_marketplace_article_rows(db=real_db_session, store_owner=owner, marketplace="ozon")}

    assert rows["7001"]["vendor_code"] == "offer-7"
    assert rows["7001"]["name"] == "Catalog item"
    assert rows["7001"]["cost_price"] == 12.5
    assert rows["7002"]["cost_price"] is None  # never a zero the owner did not enter
    assert rows["7002"]["name"] is None


# ── API: connection save starts the backfill; new read endpoints keep the WB shape ──


def test_saved_connection_reports_backfill_and_read_endpoints_follow_viewer_rules(real_db_session, dispatched, monkeypatch):
    owner = User(id=str(uuid4()), email="auto-sync-api-owner@example.com", password_hash="hash", is_active=True, tax_rate=Decimal("0.06"))
    viewer = User(id=str(uuid4()), email="auto-sync-api-viewer@example.com", password_hash="hash", is_active=True)
    real_db_session.add_all([owner, viewer])
    real_db_session.commit()
    from app.models.store_access_grant import StoreAccessGrant

    real_db_session.add(
        StoreAccessGrant(store_owner_user_id=str(owner.id), viewer_user_id=str(viewer.id), status="active", revoked_at=None)
    )
    real_db_session.commit()
    _install_db_override(real_db_session)
    monkeypatch.setattr("app.routers.marketplace_finance._today", lambda: TODAY)
    try:
        with TestClient(app) as client:
            owner_headers = {"Authorization": f"Bearer {_token(str(owner.id))}"}
            viewer_headers = {
                "Authorization": f"Bearer {_token(str(viewer.id))}",
                "X-Store-Owner-Id": str(owner.id),
            }

            saved = client.put(
                "/dashboard/marketplace/connection",
                headers=owner_headers,
                json={"client_id": "client-1", "api_key": "secret-api-key"},
            )
            assert saved.status_code == 200
            assert saved.json()["backfill"]["status"] == "running"
            assert saved.json()["backfill"]["date_from"] == "2026-01-01"
            assert len(dispatched) == 1
            queued = real_db_session.query(OzonSyncRun).filter_by(id=dispatched[0]).one()
            assert (queued.trigger, queued.date_from, queued.date_to) == ("backfill", date(2026, 1, 1), date(2026, 1, 31))
            assert "secret-api-key" not in saved.text

            query = {"marketplace": "ozon", "date_from": "2026-02-03", "date_to": "2026-02-04"}
            pnl = client.get("/dashboard/marketplace/pnl", headers=viewer_headers, params=query)
            assert pnl.status_code == 200
            assert [row["date"] for row in pnl.json()] == ["2026-02-03", "2026-02-04"]
            assert pnl.json()[0]["revenue"] is None  # no saved day: unknown, not zero

            sku = client.get("/dashboard/marketplace/sku", headers=viewer_headers, params=query)
            assert sku.status_code == 200 and sku.json() == []

            articles = client.get("/dashboard/marketplace/articles", headers=viewer_headers, params={"marketplace": "ozon"})
            assert articles.status_code == 200 and articles.json() == []

            expenses = client.get(
                "/dashboard/marketplace/expenses",
                headers=viewer_headers,
                params={"date_from": "2026-02-01", "date_to": "2026-02-28"},
            )
            assert expenses.status_code == 200 and expenses.json() == []

            wb_marketplace = client.get(
                "/dashboard/marketplace/pnl",
                headers=owner_headers,
                params={"marketplace": "wb", "date_from": "2026-02-03", "date_to": "2026-02-04"},
            )
            assert wb_marketplace.status_code == 422  # WB keeps its own /dashboard/pnl endpoint

            reversed_range = client.get(
                "/dashboard/marketplace/pnl",
                headers=owner_headers,
                params={"marketplace": "ozon", "date_from": "2026-02-05", "date_to": "2026-02-04"},
            )
            assert reversed_range.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── Celery wiring: the worker chains by run ID, the beat job queues by run ID ───


def test_worker_task_queues_the_following_chunk_by_id(real_db_session, dispatched, monkeypatch):
    from celery_app import ozon_tasks

    monkeypatch.setattr("app.services.ozon_client.OzonClient", EmptyProvider)
    monkeypatch.setattr(ozon_tasks, "SessionLocal", lambda: real_db_session)
    monkeypatch.setattr(real_db_session, "close", lambda: None)
    monkeypatch.setattr(ozon_tasks, "date", FixedDate)
    owner_id = _owner(real_db_session, "auto-sync-worker@example.com", status="configured")
    first = next_backfill_run(real_db_session, user_id=owner_id, today=TODAY)
    dispatched.clear()

    result = ozon_tasks.ozon_sync_run(str(first.id))

    assert result["status"] == "complete"
    assert len(dispatched) == 1
    follow_up = real_db_session.query(OzonSyncRun).filter_by(id=dispatched[0]).one()
    assert (follow_up.trigger, follow_up.date_from, follow_up.date_to) == ("backfill", date(2026, 2, 1), date(2026, 2, 9))


def test_daily_beat_task_queues_refresh_by_id(real_db_session, dispatched, monkeypatch):
    from celery_app import ozon_tasks

    monkeypatch.setattr(ozon_tasks, "SessionLocal", lambda: real_db_session)
    monkeypatch.setattr(real_db_session, "close", lambda: None)
    monkeypatch.setattr(ozon_tasks, "date", FixedDate)
    owner_id = _owner(real_db_session, "auto-sync-beat@example.com", status="ready")
    _finished_backfill(real_db_session, owner_id, date(2026, 1, 1), date(2026, 2, 9))
    dispatched.clear()

    assert ozon_tasks.ozon_daily_refresh() == {"queued": 1}
    assert len(dispatched) == 1
    assert real_db_session.query(OzonSyncRun).filter_by(id=dispatched[0]).one().trigger == "daily"


def test_daily_refresh_is_scheduled_at_0630_moscow_by_default():
    from celery_app.celery import celery_app

    entry = celery_app.conf.beat_schedule["ozon-daily-refresh-0630-msk"]
    assert entry["task"] == "ozon_daily_refresh"
    assert entry["schedule"].hour == {6}
    assert entry["schedule"].minute == {30}


# ── Overview: a missing detail on one posting does not blank the day; product margin needs only its own inputs ──


def test_overview_keeps_day_revenue_and_computes_product_margin_despite_another_sku_gap(real_db_session):
    from app.services.marketplace_finance_service import _replace_complete_day, build_overview
    from app.services.ozon_finance import normalize_accruals
    from tests.test_ozon_finance import posting_accrual

    owner_id = _owner(real_db_session, "auto-sync-product-margin@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    day, next_day = date(2026, 2, 3), date(2026, 2, 4)
    known = posting_accrual(901, total="166.00", sale="200.00", commission="-30.00", delivery="-4.00", sku="7001", day=day.isoformat())
    missing = posting_accrual(902, total="-4.00", sale="0.00", commission="0.00", delivery="-4.00", sku="7002", day=day.isoformat())
    del missing["posting"]["products"][0]["commission"]
    clean_next_day = posting_accrual(903, total="86.00", sale="100.00", commission="-10.00", delivery="-4.00", sku="7001", day=next_day.isoformat())
    real_db_session.add(OzonProductCost(user_id=owner_id, sku="7001", cost_price=Decimal("20.00")))
    for accruals, target in (([known, missing], day), ([clean_next_day], next_day)):
        normalized = normalize_accruals(accruals, [], costs={"7001": Decimal("20.00")})
        _replace_complete_day(
            real_db_session,
            user_id=owner_id,
            day=target,
            accruals=accruals,
            normalized=normalized,
            catalogue=[],
            warnings=normalized["warnings"],
        )

    result = build_overview(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=day, date_to=next_day)

    gap_day, clean_day = result["daily"]
    assert gap_day["revenue"] == 200.0  # the known posting still counts
    assert gap_day["cost_known_revenue_share"] == 1.0  # every rouble of revenue has a known cost; SKU 7002 sold nothing
    assert clean_day["revenue"] == 100.0
    assert clean_day["cost_known_revenue_share"] == 1.0  # every rouble of this day's revenue has a known SKU cost
    products = {product["sku"]: product for product in result["products"]}
    sku_day = next(row for row in products["7001"]["daily"] if row["date"] == day.isoformat())
    assert sku_day["cogs"] == 20.0
    assert sku_day["marketplace_net"] == 166.0
    assert sku_day["tax"] == 12.0  # revenue × the owner's WB tax rate
    assert sku_day["margin"] == 134.0  # net − COGS − tax, per product and day
    # SKU 7002 has no entered cost, so it has no margin at all (as in the reference workbook), though its charge
    # still reduces the day: 166 − 4 − COGS 20 − tax 12 = 130.
    assert products["7002"]["daily"][0]["margin"] is None
    assert products["7002"]["daily"][0]["logistics"] == 4.0
    assert gap_day["margin"] == 130.0


def test_pnl_row_sums_only_skus_with_known_cost_once_operating_expenses_are_confirmed(real_db_session):
    from app.models.ozon import OzonFinanceSettings
    from app.services.marketplace_finance_service import _replace_complete_day, build_marketplace_pnl_rows
    from app.services.ozon_finance import normalize_accruals
    from tests.test_ozon_finance import posting_accrual

    owner_id = _owner(real_db_session, "auto-sync-pnl-known-cost@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    real_db_session.add(OzonFinanceSettings(user_id=owner_id, operation_expenses_confirmed=True))
    real_db_session.commit()
    day = date(2026, 2, 3)
    known = posting_accrual(921, total="166.00", sale="200.00", commission="-30.00", delivery="-4.00", sku="7001", day=day.isoformat())
    missing = posting_accrual(922, total="-4.00", sale="0.00", commission="0.00", delivery="-4.00", sku="7002", day=day.isoformat())
    del missing["posting"]["products"][0]["commission"]
    accruals = [known, missing]
    normalized = normalize_accruals(accruals, [], costs={"7001": Decimal("20.00")})
    _replace_complete_day(
        real_db_session,
        user_id=owner_id,
        day=day,
        accruals=accruals,
        normalized=normalized,
        catalogue=[],
        warnings=normalized["warnings"],
    )

    [row] = build_marketplace_pnl_rows(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=day, date_to=day)

    assert row["revenue"] == 200.0  # the known posting; the gap posting does not blank the day
    assert row["cogs"] == 20.0  # only SKU 7001 has a cost
    assert row["margin"] == 130.0  # SKU 7001 134 + SKU 7002 delivery line −4 − 0 expenses
    assert row["known_cost_revenue"] == 200.0  # SKU 7002's revenue is unknown, so it is outside this subset


def test_real_shaped_product_row_gets_margin_from_its_own_fields_without_allocation_flags(real_db_session):
    from app.services.marketplace_finance_service import build_overview

    owner_id = _owner(real_db_session, "auto-sync-real-product-shape@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    day = date(2026, 1, 5)
    # Stored product row exactly as the real-data check showed it (2026-10-25). Its false flags are day-level
    # attribution gaps; margin must not depend on them.
    real_product = {
        "sku": "1504825252",
        "cogs": "1100.00",
        "revenue": "4500",
        "commission": "2340",
        "logistics": "286",
        "acquiring": "25.79",
        "sold_quantity": "1",
        "returned_quantity": "0",
        "net_quantity": "1",
        "margin": None,
        "tax": None,
        "profitability_complete": False,
        "sku_allocation_complete": False,
        "accrual_classification_complete": True,
    }
    real_db_session.add(OzonProductCost(user_id=owner_id, sku="1504825252", cost_price=Decimal("1100.00")))
    real_db_session.add(
        OzonSyncDay(
            user_id=owner_id,
            day=day,
            status="complete",
            last_attempt_status="succeeded",
            daily_data=_day_values(
                revenue="4500.00",
                commission="2340.00",
                logistics="286.00",
                acquiring="25.79",
                cogs="1100.00",
                marketplace_net="1848.21",
                unallocated_amount="0.00",
                reconciliation_difference="0.00",
            ),
            products_data=[real_product],
            warnings=[],
        )
    )
    real_db_session.commit()

    result = build_overview(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=day, date_to=day)

    sku = result["products"][0]
    assert sku["sku"] == "1504825252"
    sku_day = sku["daily"][0]
    assert sku_day["tax"] == 270.0  # 4500 × 6%
    # 4500 − 2340 − 286 − 25.79 − 1100 − 270 = 478.21; absent charges are zero, not unknown.
    assert sku_day["margin"] == 478.21
    day_row = result["daily"][0]
    assert day_row["revenue"] == 4500.0
    assert day_row["cogs"] == 1100.0
    assert day_row["known_cost_revenue"] == 4500.0
    assert day_row["margin"] == 478.21  # balance 1848.21 − COGS 1100 − tax 270 − expenses 0


def test_complete_day_without_sale_postings_keeps_zero_revenue_and_its_charges(real_db_session):
    from app.services.marketplace_finance_service import _replace_complete_day, build_overview
    from app.services.ozon_finance import normalize_accruals

    owner_id = _owner(real_db_session, "auto-sync-empty-sales-day@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    day = date(2026, 1, 1)
    charge = {
        "accrual_id": 9101,
        "date": day.isoformat(),
        "accrued_category": "NON_ITEM",
        "total_amount": {"amount": "-97.00", "currency": "RUB"},
        "non_item_fee": {"type_id": 12, "accrued": {"amount": "-97.00", "currency": "RUB"}},
    }
    types = [{"id": 12, "name": "CrossDock", "description": "cross dock"}]
    normalized = normalize_accruals([charge], types)
    _replace_complete_day(
        real_db_session,
        user_id=owner_id,
        day=day,
        accruals=[charge],
        normalized=normalized,
        catalogue=[],
        warnings=normalized["warnings"],
    )

    result = build_overview(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=day, date_to=day)

    assert result["coverage"] == {"complete": True, "missing_dates": []}
    day_row = result["daily"][0]
    assert day_row["revenue"] == 0.0  # a complete day with no sales is a known zero, not an unknown
    assert day_row["logistics"] == 97.0
    assert day_row["cogs"] == 0.0
    assert day_row["margin"] == -97.0  # the not-tied charge reduces the day
    assert day_row["known_cost_revenue"] == 0.0
    assert result["products"] == []


def test_known_not_tied_charge_does_not_blank_sku_margin_and_reduces_only_the_day(real_db_session):
    from app.services.marketplace_finance_service import _replace_complete_day, build_overview
    from app.services.ozon_finance import normalize_accruals
    from tests.test_ozon_finance import posting_accrual

    owner_id = _owner(real_db_session, "auto-sync-not-tied-charge@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    day = date(2026, 1, 2)
    sale = posting_accrual(9201, total="166.00", sale="200.00", commission="-30.00", delivery="-4.00", sku="7001", day=day.isoformat())
    charge = {
        "accrual_id": 9202,
        "date": day.isoformat(),
        "accrued_category": "NON_ITEM",
        "total_amount": {"amount": "-97.00", "currency": "RUB"},
        "non_item_fee": {"type_id": 12, "accrued": {"amount": "-97.00", "currency": "RUB"}},
    }
    accruals = [sale, charge]
    types = [{"id": 12, "name": "CrossDock", "description": "cross dock"}]
    real_db_session.add(OzonProductCost(user_id=owner_id, sku="7001", cost_price=Decimal("20.00")))
    normalized = normalize_accruals(accruals, types, costs={"7001": Decimal("20.00")})
    _replace_complete_day(
        real_db_session,
        user_id=owner_id,
        day=day,
        accruals=accruals,
        normalized=normalized,
        catalogue=[],
        warnings=normalized["warnings"],
    )

    result = build_overview(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=day, date_to=day)

    sku_day = next(row for row in next(p for p in result["products"] if p["sku"] == "7001")["daily"] if row["date"] == day.isoformat())
    # 200 − 30 − 4 − COGS 20 − tax 12 = 134: the not-tied charge is not spread onto this SKU.
    assert sku_day["margin"] == 134.0
    assert result["daily"][0]["margin"] == 37.0  # balance 69 − COGS 20 − tax 12 − expenses 0


def test_sku_without_catalogue_card_is_left_out_of_products_and_day_totals(real_db_session):
    from app.services.marketplace_finance_service import _replace_complete_day, build_overview
    from app.services.ozon_finance import normalize_accruals
    from tests.test_ozon_finance import posting_accrual

    owner_id = _owner(real_db_session, "auto-sync-uncatalogued-sku@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    day = date(2026, 1, 6)
    carded = posting_accrual(9301, total="166.00", sale="200.00", commission="-30.00", delivery="-4.00", sku="7001", day=day.isoformat())
    bare = posting_accrual(9302, total="43.00", sale="50.00", commission="-5.00", delivery="-2.00", sku="7999", day=day.isoformat())
    charge = {
        "accrual_id": 9303,
        "date": day.isoformat(),
        "accrued_category": "NON_ITEM",
        "total_amount": {"amount": "-10.00", "currency": "RUB"},
        "non_item_fee": {"type_id": 12, "accrued": {"amount": "-10.00", "currency": "RUB"}},
    }
    accruals = [carded, bare, charge]
    types = [{"id": 12, "name": "CrossDock", "description": "cross dock"}]
    real_db_session.add(OzonProductCost(user_id=owner_id, sku="7001", cost_price=Decimal("20.00")))
    normalized = normalize_accruals(accruals, types, costs={"7001": Decimal("20.00")})
    _replace_complete_day(
        real_db_session,
        user_id=owner_id,
        day=day,
        accruals=accruals,
        normalized=normalized,
        catalogue=[{"sku": "7001", "offer_id": "offer-7001", "name": "Carded product"}],
        warnings=normalized["warnings"],
    )

    result = build_overview(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=day, date_to=day)

    # Owner decision (2026-10-25): SKU 7999 has no catalogue card, so neither its row nor its money is reported.
    assert [product["sku"] for product in result["products"]] == ["7001"]
    day_row = result["daily"][0]
    assert day_row["revenue"] == 200.0
    assert day_row["commission"] == 30.0
    assert day_row["logistics"] == 14.0  # SKU 7001 delivery 4 + the not-tied cross-dock 10 stays with the day
    assert day_row["sold_quantity"] == 1.0
    assert day_row["marketplace_net"] == 156.0  # 199 from Ozon − SKU 7999's own 43
    assert day_row["tax"] == 12.0
    assert day_row["margin"] == 124.0  # 156 − COGS 20 − tax 12


def test_day_of_net_returns_has_negative_tax_so_taxes_add_up_to_rate_times_period_revenue(real_db_session):
    from app.services.marketplace_finance_service import _replace_complete_day, build_overview
    from app.services.ozon_finance import normalize_accruals
    from tests.test_ozon_finance import posting_accrual

    owner_id = _owner(real_db_session, "auto-sync-signed-tax@example.com")
    owner = real_db_session.query(User).filter_by(id=owner_id).one()
    sale_day, return_day = date(2026, 1, 7), date(2026, 1, 8)
    sale = posting_accrual(9401, total="166.00", sale="200.00", commission="-30.00", delivery="-4.00", sku="7001", day=sale_day.isoformat())
    refund = posting_accrual(9402, total="-85.00", sale="-100.00", commission="15.00", delivery="0.00", sku="7001", day=return_day.isoformat())
    refund["posting"]["products"][0]["delivery"] = None  # real return postings carry a null delivery block
    real_db_session.add(OzonProductCost(user_id=owner_id, sku="7001", cost_price=Decimal("20.00")))
    for accruals, target in (([sale], sale_day), ([refund], return_day)):
        normalized = normalize_accruals(accruals, [], costs={"7001": Decimal("20.00")})
        _replace_complete_day(
            real_db_session,
            user_id=owner_id,
            day=target,
            accruals=accruals,
            normalized=normalized,
            catalogue=[],
            warnings=normalized["warnings"],
        )

    result = build_overview(db=real_db_session, store_owner=owner, marketplace="ozon", date_from=sale_day, date_to=return_day)

    first, second = result["daily"]
    assert first["tax"] == 12.0
    assert second["tax"] == -6.0  # owner decision (2026-10-25): returns lower the tax, no zeroing of the day
    assert second["cogs"] == -20.0  # the returned unit goes back to stock
    assert second["logistics"] == 0.0
    assert second["margin"] == -59.0  # balance −85 − COGS (−20) − tax (−6)
    sku_days = result["products"][0]["daily"]
    assert sum(row["tax"] for row in sku_days) == 6.0  # 6% of period revenue 100
    assert [row["margin"] for row in sku_days] == [134.0, -59.0]


def test_recompute_script_renormalises_stored_days_of_an_owner_without_provider_calls(real_db_session, monkeypatch):
    from app.services.marketplace_finance_service import _replace_complete_day
    from app.services.ozon_finance import normalize_accruals
    from scripts import recompute_ozon_days as script
    from tests.test_ozon_finance import posting_accrual

    owner_id = _owner(real_db_session, "auto-sync-recompute-script@example.com")
    day = date(2026, 1, 9)
    accrual = posting_accrual(9301, total="166.00", sale="200.00", commission="-30.00", delivery="-4.00", sku="7001", day=day.isoformat())
    normalized = normalize_accruals([accrual], [], costs={"7001": Decimal("20.00")})
    normalized["accrual_types"] = []
    _replace_complete_day(
        real_db_session,
        user_id=owner_id,
        day=day,
        accruals=[accrual],
        normalized=normalized,
        catalogue=[],
        warnings=[],
    )
    monkeypatch.setattr(script, "SessionLocal", lambda: real_db_session)
    monkeypatch.setattr(real_db_session, "close", lambda: None)

    assert script.owners_with_complete_days(real_db_session) == [owner_id]
    assert script.recompute_owner(real_db_session, owner_id) == 1
    assert script.main(["--user-id", owner_id]) == 0
