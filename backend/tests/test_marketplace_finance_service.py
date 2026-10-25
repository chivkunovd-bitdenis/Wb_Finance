from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.crypto import encrypt_text
from app.models.base import Base
from app.models.article import Article
from app.models.ozon import (
    OzonAccrualSnapshot,
    OzonConnection,
    OzonFinanceSettings,
    OzonOperationalExpense,
    OzonProductCost,
    OzonSyncDay,
    OzonSyncRun,
)
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User
from app.services.marketplace_finance_service import (
    MarketplaceFinanceError,
    _dedupe_accruals,
    _lock_key,
    _replace_complete_day,
    build_overview,
    enqueue_sync,
    get_product_cost,
    get_marketplace_settings,
    run_sync_run,
    set_ozon_operational_expense,
    set_product_cost,
    update_marketplace_settings,
)
from app.services.ozon_finance import normalize_accruals


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


@pytest.fixture
def committed_cost_db_session(real_db_session):
    """Use a committed private schema so independent PostgreSQL lock sessions see its tables."""
    source_engine = real_db_session.get_bind().engine
    schema_name = f"ozon_cost_test_{uuid4().hex}"
    setup_engine = create_engine(source_engine.url, pool_pre_ping=True)
    isolated_engine = None
    try:
        with setup_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.execute(text(f'SET search_path TO "{schema_name}"'))
            Base.metadata.create_all(bind=connection)

        isolated_engine = create_engine(
            source_engine.url,
            connect_args={"options": f"-c search_path={schema_name}"},
            pool_pre_ping=True,
        )
        with isolated_engine.connect() as connection:
            assert connection.execute(text("SHOW search_path")).scalar() == schema_name
            assert connection.execute(text("SELECT current_schema()")).scalar() == schema_name
            assert connection.execute(text("SELECT to_regclass('users') IS NOT NULL")).scalar() is True
        session = sessionmaker(autocommit=False, autoflush=False, bind=isolated_engine)()
        try:
            yield session
        finally:
            session.rollback()
            session.close()
    finally:
        if isolated_engine is not None:
            isolated_engine.dispose()
        with setup_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        setup_engine.dispose()


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


def test_ozon_settings_drive_only_ozon_tax_and_expenses(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-ozon-settings-economics@example.com", wb_api_key="present")
    owner_id = str(owner.id)
    day = date(2026, 9, 15)
    real_db_session.add(owner)
    real_db_session.commit()
    assert get_marketplace_settings(db=real_db_session, user_id=owner_id) == {
        "tax_rate": 0.06,
        "tax_rate_source": "wb",
        "operation_expenses_confirmed": False,
    }
    update_marketplace_settings(
        db=real_db_session,
        user_id=owner_id,
        operation_expenses_confirmed=True,
    )
    set_ozon_operational_expense(
        db=real_db_session,
        user_id=owner_id,
        expense_date=day,
        amount=Decimal("5.00"),
    )
    set_ozon_operational_expense(
        db=real_db_session,
        user_id=owner_id,
        expense_date=day,
        amount=Decimal("5.00"),
    )
    assert real_db_session.query(OzonOperationalExpense).filter_by(user_id=owner_id).count() == 1
    real_db_session.add_all(
        [
            _connection(owner_id),
            OzonProductCost(user_id=owner_id, sku="oz-7", cost_price=Decimal("20.00")),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                daily_data=_day_values(
                    revenue="200.00",
                    cogs="20.00",
                    tax=None,
                    operation_expenses=None,
                    margin=None,
                    marketplace_net="150.00",
                    reconciliation_difference="0.00",
                    accrual_classification_complete=True,
                    sku_allocation_complete=True,
                    profitability_complete=True,
                ),
                products_data=[
                    {
                        "sku": "oz-7",
                        "revenue": "200.00",
                        "cogs": "20.00",
                        "tax": None,
                        "operation_expenses": None,
                        "marketplace_net": "185.00",
                        "accrual_classification_complete": True,
                        "sku_allocation_complete": True,
                        "profitability_complete": True,
                    }
                ],
                warnings=[],
            ),
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
        ]
    )
    real_db_session.commit()

    ozon = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )
    all_markets = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="all",
        date_from=day,
        date_to=day,
    )
    assert ozon["daily"][0]["tax"] == 12.0
    assert ozon["daily"][0]["operation_expenses"] == 5.0
    # Owner rule (2026-10-25): day margin = Ozon's balance for the day 150 − known COGS 20 − tax 12 − entered
    # expenses 5 = 113, with no confirmation gate. The balance is the authoritative total, not a sum of SKU rows.
    assert ozon["daily"][0]["margin"] == 113.0
    assert ozon["accounting_basis"]["margin"] == "current_cost_estimate"
    assert ozon["accounting_basis"]["costs"] == "current_sku_cost_restates_all_dates"
    assert all_markets["accounting_basis"]["wb_fully_reconciled"] is False
    assert all_markets["accounting_basis"]["margin"] == "preliminary_wb_and_ozon"
    assert any("старый импорт WB" in warning for warning in all_markets["warnings"])
    assert ozon["products"][0]["daily"][0]["tax"] == 12.0
    assert ozon["products"][0]["daily"][0]["margin"] == 168.0  # 200 − 0 − 20 − 12
    assert ozon["products"][0]["daily"][0]["operation_expenses"] is None
    assert all_markets["daily"][0]["tax"] == 27.0
    assert all_markets["daily"][0]["operation_expenses"] == 8.0
    assert all_markets["daily"][0]["margin"] == 215.0  # WB saved margin 102 + Ozon day margin 113

    # Ozon tax follows the owner’s existing WB setting on the next GET.
    owner.tax_rate = Decimal("0.08")
    real_db_session.commit()
    changed_rate = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )
    assert changed_rate["daily"][0]["tax"] == 16.0
    assert changed_rate["daily"][0]["margin"] == 109.0  # balance 150 − 20 − 16 − 5
    assert changed_rate["products"][0]["daily"][0]["tax"] == 16.0
    assert get_marketplace_settings(db=real_db_session, user_id=owner_id)["tax_rate"] == 0.08
    assert real_db_session.query(PnlDaily).filter_by(user_id=owner_id, date=day).one().tax == Decimal("15.00")


def test_ozon_margin_subtracts_entered_operating_expenses_without_a_confirmation_gate(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-ozon-profit-unknown@example.com")
    day = date(2026, 9, 16)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(str(owner.id)),
            OzonSyncDay(
                user_id=str(owner.id),
                day=day,
                status="complete",
                daily_data=_day_values(
                    cogs="20.00",
                    marketplace_net="70.00",
                    reconciliation_difference="0.00",
                    accrual_classification_complete=True,
                    sku_allocation_complete=True,
                    profitability_complete=True,
                ),
                # One SKU with a known cost: balance 70 − COGS 20 − tax 6 = 44 is the day's margin before expenses.
                products_data=[
                    {
                        **_day_values(revenue="100.00", cogs="20.00", marketplace_net="70.00"),
                        "sku": "7001",
                        "accrual_classification_complete": True,
                        "sku_allocation_complete": True,
                        "profitability_complete": True,
                    }
                ],
                warnings=[],
            ),
            OzonFinanceSettings(
                user_id=str(owner.id),
                operation_expenses_confirmed=False,
            ),
            OzonOperationalExpense(
                user_id=str(owner.id),
                date=day,
                amount=Decimal("5.00"),
            ),
        ]
    )
    real_db_session.commit()

    result = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )
    # Owner decision (2026-10-25): no confirmation checkbox gates the margin; entered expenses are subtracted as WB does.
    assert result["daily"][0]["tax"] == 6.0
    assert result["daily"][0]["operation_expenses"] == 5.0
    assert result["daily"][0]["cogs"] == 20.0
    # Day: balance 70 − COGS 20 − tax 6 − entered expenses 5 = 39.
    assert result["daily"][0]["margin"] == 39.0


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
                daily_data=_day_values(
                    revenue="100.00",
                    commission="10.00",
                    logistics="4.00",
                    cogs="30.00",
                    tax=None,
                    margin=None,
                    marketplace_net="85.00",
                    acquiring="2.00",
                    sold_quantity="3",
                    returned_quantity="1",
                    net_quantity="2",
                    accrual_classification_complete=True,
                    sku_allocation_complete=True,
                    profitability_complete=True,
                ),
                products_data=[
                    {
                        "sku": "7001",
                        "revenue": "100.00",
                        "acquiring": "2.00",
                        "cogs": "30.00",
                        "marketplace_net": "85.00",
                        "sold_quantity": "3",
                        "returned_quantity": "1",
                        "net_quantity": "2",
                        "accrual_classification_complete": True,
                        "sku_allocation_complete": True,
                        "profitability_complete": True,
                    }
                ],
                warnings=[],
            ),
            OzonProductCost(user_id=owner_id, sku="7001", cost_price=30),
            OzonFinanceSettings(
                user_id=owner_id,
                operation_expenses_confirmed=True,
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
    ozon_only = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )

    assert result["coverage"]["complete"] is True
    assert result["daily"][0]["revenue"] == 350.0
    assert result["daily"][0]["commission"] == 35.0
    assert result["daily"][0]["operation_expenses"] == 3.0
    assert result["daily"][0]["tax"] == 21.0
    # WB saved margin 102 + Ozon day margin 49 (balance 85 − COGS 30 − tax 6).
    assert result["daily"][0]["margin"] == 151.0
    assert result["daily"][0]["acquiring"] is None
    assert result["daily"][0]["sold_quantity"] is None
    assert ozon_only["daily"][0]["acquiring"] == 2.0
    assert ozon_only["daily"][0]["sold_quantity"] == 3.0
    assert ozon_only["daily"][0]["returned_quantity"] == 1.0
    assert ozon_only["daily"][0]["net_quantity"] == 2.0
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


def test_real_posting_cost_entry_recomputes_margin_and_clear_restores_unknown_without_provider_calls(
    real_db_session, monkeypatch
):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-real-cost-recompute@example.com", wb_api_key="present")
    other_owner = _user("marketplace-real-cost-other-owner@example.com")
    owner_id = str(owner.id)
    other_owner_id = str(other_owner.id)
    day = date(2026, 10, 6)
    real_db_session.add_all([owner, other_owner])
    real_db_session.commit()

    unrelated_warning = "Не удалось обновить каталог товаров; сохранены последние названия."
    other_daily = _day_values(
        revenue="55.00",
        cogs="7.00",
        margin="30.00",
        marketplace_net="45.00",
        reconciliation_difference="0.00",
        accrual_classification_complete=True,
        sku_allocation_complete=True,
        profitability_complete=True,
    )
    other_products = [{"sku": "7010", "revenue": "55.00", "cogs": "7.00", "margin": "30.00"}]
    real_db_session.add_all(
        [
            _connection(owner_id),
            _connection(other_owner_id),
            OzonFinanceSettings(user_id=owner_id, operation_expenses_confirmed=True),
            OzonFinanceSettings(user_id=other_owner_id, operation_expenses_confirmed=True),
            OzonOperationalExpense(user_id=owner_id, date=day, amount=Decimal("3.00")),
            OzonOperationalExpense(user_id=other_owner_id, date=day, amount=Decimal("4.00")),
            OzonProductCost(user_id=other_owner_id, sku="7010", cost_price=Decimal("7.00")),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                source_count=1,
                daily_data=_day_values(),
                products_data=[{"sku": "7010", "cogs": None}],
                accrual_types=[],
            ),
            OzonSyncDay(
                user_id=other_owner_id,
                day=day,
                status="complete",
                source_count=0,
                daily_data=other_daily,
                products_data=other_products,
                accrual_types=[],
                warnings=["чужой снимок"],
            ),
            PnlDaily(
                user_id=owner_id,
                date=day,
                revenue=Decimal("90.00"),
                commission=Decimal("9.00"),
                logistics=Decimal("2.00"),
                penalties=0,
                storage=0,
                ads_spend=0,
                cogs=Decimal("30.00"),
                tax=Decimal("5.40"),
                operation_expenses=Decimal("2.00"),
                margin=Decimal("41.60"),
            ),
            PnlDaily(
                user_id=other_owner_id,
                date=day,
                revenue=Decimal("70.00"),
                commission=Decimal("7.00"),
                logistics=Decimal("2.00"),
                penalties=0,
                storage=0,
                ads_spend=0,
                cogs=Decimal("20.00"),
                tax=Decimal("4.20"),
                operation_expenses=Decimal("1.00"),
                margin=Decimal("35.80"),
            ),
        ]
    )
    real_db_session.commit()

    raw = {
        "accrual_id": "cost-recompute-posting-1",
        "date": day.isoformat(),
        "unit_number": "posting-cost-recompute-1",
        "accrued_category": "POSTING",
        "total_amount": {"amount": "81.00", "currency": "RUB"},
        "posting": {
            "products": [
                {
                    "sku": "7010",
                    "quantity": 1,
                    "commission": {
                        "sale_amount": {"amount": "100.00", "currency": "RUB"},
                        "commission": {"amount": "-15.00", "currency": "RUB"},
                        "sale_commission": {"amount": "-17.00", "currency": "RUB"},
                        "bonus": {"amount": "-2.00", "currency": "RUB"},
                        "coinvestment": {"amount": "-3.00", "currency": "RUB"},
                        "sale_price": {"amount": "120.00", "currency": "RUB"},
                        "seller_price": {"amount": "100.00", "currency": "RUB"},
                    },
                    "delivery": {
                        "total_accrued": {"amount": "-4.00", "currency": "RUB"},
                        "services": [{"type_id": 501, "accrued": {"amount": "-4.00", "currency": "RUB"}}],
                    },
                }
            ]
        },
    }
    normalized = normalize_accruals([raw], [], costs={})
    normalized["accrual_types"] = []
    _replace_complete_day(
        real_db_session,
        user_id=owner_id,
        day=day,
        accruals=[raw],
        normalized=normalized,
        catalogue=[{"sku": "7010", "offer_id": "offer-7010", "name": "Test product"}],
        warnings=[*normalized["warnings"], unrelated_warning],
    )

    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    cost_warning = f"{day}: себестоимость неизвестна из-за неполного количества, возврата или отсутствующей себестоимости"
    assert state.daily_data["cogs"] is None
    assert cost_warning in state.warnings
    other_state = real_db_session.query(OzonSyncDay).filter_by(user_id=other_owner_id, day=day).one()
    other_snapshot_before = (dict(other_state.daily_data), list(other_state.products_data), list(other_state.warnings))
    owner_wb_before = real_db_session.query(PnlDaily).filter_by(user_id=owner_id, date=day).one()
    owner_wb_values = (owner_wb_before.cogs, owner_wb_before.tax, owner_wb_before.operation_expenses, owner_wb_before.margin)
    other_wb_before = real_db_session.query(PnlDaily).filter_by(user_id=other_owner_id, date=day).one()
    other_wb_values = (other_wb_before.cogs, other_wb_before.tax, other_wb_before.operation_expenses, other_wb_before.margin)

    class UnexpectedProviderCall:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Cost recomputation must not call the Ozon API")

    monkeypatch.setattr("app.services.ozon_client.OzonClient", UnexpectedProviderCall)

    initial = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )
    # As in WB, a SKU without an entered cost adds nothing to the day's COGS: balance 81 − 0 − tax 6 − expenses 3.
    # The SKU itself has no margin until its cost is entered.
    assert initial["daily"][0]["cogs"] == 0.0
    assert initial["daily"][0]["known_cost_revenue"] == 0.0
    assert initial["daily"][0]["margin"] == 72.0
    assert next(row for row in initial["products"] if row["sku"] == "7010")["daily"][0]["margin"] is None

    with monkeypatch.context() as no_post_commit_read:
        no_post_commit_read.setattr(
            "app.services.marketplace_finance_service.get_product_cost",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("Cost response must not query after commit")),
        )
        saved_cost = set_product_cost(
            db=real_db_session,
            user_id=owner_id,
            sku="7010",
            cost_price=Decimal("20.00"),
        )
    assert saved_cost == Decimal("20.00")
    real_db_session.expire_all()
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.daily_data["cogs"] == "20.00"
    assert cost_warning not in state.warnings
    assert unrelated_warning in state.warnings

    after_entry = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )
    assert after_entry["daily"][0]["cogs"] == 20.0
    assert after_entry["daily"][0]["tax"] == 6.0
    assert after_entry["daily"][0]["operation_expenses"] == 3.0
    assert after_entry["daily"][0]["margin"] == 52.0
    product = next(row for row in after_entry["products"] if row["sku"] == "7010")
    assert product["daily"][0]["cogs"] == 20.0
    assert product["daily"][0]["margin"] == 55.0

    set_product_cost(db=real_db_session, user_id=owner_id, sku="7010", cost_price=None)
    real_db_session.expire_all()
    state = real_db_session.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
    assert state.daily_data["cogs"] is None
    assert cost_warning in state.warnings
    assert unrelated_warning in state.warnings
    cleared = build_overview(
        db=real_db_session,
        store_owner=owner,
        marketplace="ozon",
        date_from=day,
        date_to=day,
    )
    assert cleared["daily"][0]["cogs"] == 0.0
    assert cleared["daily"][0]["margin"] == 72.0
    cleared_product = next(row for row in cleared["products"] if row["sku"] == "7010")
    assert cleared_product["daily"][0]["cogs"] is None
    assert cleared_product["daily"][0]["margin"] is None

    real_db_session.expire_all()
    other_state = real_db_session.query(OzonSyncDay).filter_by(user_id=other_owner_id, day=day).one()
    assert (dict(other_state.daily_data), list(other_state.products_data), list(other_state.warnings)) == other_snapshot_before
    assert get_product_cost(db=real_db_session, user_id=other_owner_id, sku="7010") == Decimal("7.00")
    owner_wb_after = real_db_session.query(PnlDaily).filter_by(user_id=owner_id, date=day).one()
    assert (owner_wb_after.cogs, owner_wb_after.tax, owner_wb_after.operation_expenses, owner_wb_after.margin) == owner_wb_values
    other_wb_after = real_db_session.query(PnlDaily).filter_by(user_id=other_owner_id, date=day).one()
    assert (other_wb_after.cogs, other_wb_after.tax, other_wb_after.operation_expenses, other_wb_after.margin) == other_wb_values


def test_cost_and_saved_ledger_rollback_together_when_recalculation_fails(committed_cost_db_session, monkeypatch):
    db = committed_cost_db_session
    owner = _user("marketplace-cost-atomic-failure@example.com")
    owner_id = str(owner.id)
    day = date(2026, 10, 5)
    saved_daily = _day_values(
        cogs="8.00",
        margin="50.00",
        accrual_classification_complete=True,
        sku_allocation_complete=True,
        profitability_complete=True,
        sold_quantity="1",
        returned_quantity="0",
        net_quantity="1",
    )
    saved_products = [{"sku": "7010", "cogs": "8.00", "margin": "50.00"}]
    saved_warnings = ["Сохранённое предупреждение"]
    raw_payload = {
        "accrual_id": "atomic-cost-source",
        "date": day.isoformat(),
        "accrued_category": "POSTING",
        "total_amount": {"amount": "85.00", "currency": "RUB"},
    }
    db.add(owner)
    db.commit()
    db.add_all(
        [
            OzonProductCost(user_id=owner_id, sku="7010", cost_price=Decimal("8.00")),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                source_count=1,
                daily_data=saved_daily,
                products_data=saved_products,
                accrual_types=[],
                warnings=saved_warnings,
            ),
            OzonAccrualSnapshot(
                user_id=owner_id,
                accrual_id="atomic-cost-source",
                accrual_date=day,
                category="POSTING",
                currency="RUB",
                raw_payload=raw_payload,
            ),
        ]
    )
    db.commit()

    def normalized_with_updated_daily(*args, **kwargs):
        return {
            "daily": [
                {
                    **_day_values(cogs="20.00", margin="38.00"),
                    "date": day.isoformat(),
                    "accrual_classification_complete": True,
                    "sku_allocation_complete": True,
                    "profitability_complete": True,
                    "sold_quantity": Decimal("1"),
                    "returned_quantity": Decimal("0"),
                    "net_quantity": Decimal("1"),
                }
            ],
            "products": [],
            "warnings": [],
        }

    def fail_after_daily_is_staged(*args, **kwargs):
        raise RuntimeError("simulated product serialization failure")

    monkeypatch.setattr("app.services.ozon_finance.normalize_accruals", normalized_with_updated_daily)
    monkeypatch.setattr(
        "app.services.marketplace_finance_service._normalizer_products_for_day",
        fail_after_daily_is_staged,
    )
    try:
        with pytest.raises(RuntimeError, match="simulated product serialization failure"):
            set_product_cost(db=db, user_id=owner_id, sku="7010", cost_price=Decimal("20.00"))

        db.expire_all()
        state = db.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
        snapshot = db.query(OzonAccrualSnapshot).filter_by(user_id=owner_id, accrual_id="atomic-cost-source").one()
        assert get_product_cost(db=db, user_id=owner_id, sku="7010") == Decimal("8.00")
        assert state.daily_data == saved_daily
        assert state.products_data == saved_products
        assert state.warnings == saved_warnings
        assert snapshot.raw_payload == raw_payload
    finally:
        db.rollback()


def test_cost_change_is_blocked_by_active_sync_or_held_owner_lock(committed_cost_db_session):
    db = committed_cost_db_session
    owner = _user("marketplace-cost-sync-conflict@example.com")
    owner_id = str(owner.id)
    day = date(2026, 10, 4)
    saved_daily = _day_values(cogs="8.00", margin="50.00")
    saved_products = [{"sku": "7010", "cogs": "8.00", "margin": "50.00"}]
    raw_payload = {
        "accrual_id": "sync-conflict-source",
        "date": day.isoformat(),
        "accrued_category": "POSTING",
        "total_amount": {"amount": "85.00", "currency": "RUB"},
    }
    db.add(owner)
    db.commit()
    run = OzonSyncRun(user_id=owner_id, date_from=day, date_to=day, status="queued", progress=0)
    db.add_all(
        [
            run,
            OzonProductCost(user_id=owner_id, sku="7010", cost_price=Decimal("8.00")),
            OzonSyncDay(
                user_id=owner_id,
                day=day,
                status="complete",
                source_count=1,
                daily_data=saved_daily,
                products_data=saved_products,
                accrual_types=[],
                warnings=["saved warning"],
            ),
            OzonAccrualSnapshot(
                user_id=owner_id,
                accrual_id="sync-conflict-source",
                accrual_date=day,
                category="POSTING",
                currency="RUB",
                raw_payload=raw_payload,
            ),
        ]
    )
    db.commit()

    expected_conflict = "Синхронизация уже ожидает запуска или выполняется"
    try:
        with pytest.raises(MarketplaceFinanceError, match=expected_conflict):
            set_product_cost(db=db, user_id=owner_id, sku="7010", cost_price=Decimal("20.00"))

        db.expire_all()
        assert get_product_cost(db=db, user_id=owner_id, sku="7010") == Decimal("8.00")
        state = db.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
        assert state.daily_data == saved_daily
        assert state.products_data == saved_products
        snapshot = db.query(OzonAccrualSnapshot).filter_by(user_id=owner_id, accrual_id="sync-conflict-source").one()
        assert snapshot.raw_payload == raw_payload

        run.status = "complete"
        db.commit()
        lock_connection = db.get_bind().connect()
        try:
            lock_connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": _lock_key(owner_id)})
            lock_connection.commit()
            with pytest.raises(MarketplaceFinanceError, match=expected_conflict):
                set_product_cost(db=db, user_id=owner_id, sku="7010", cost_price=Decimal("20.00"))
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": _lock_key(owner_id)})
            lock_connection.commit()
            lock_connection.close()

        db.expire_all()
        assert get_product_cost(db=db, user_id=owner_id, sku="7010") == Decimal("8.00")
        state = db.query(OzonSyncDay).filter_by(user_id=owner_id, day=day).one()
        assert state.daily_data == saved_daily
        assert state.products_data == saved_products
    finally:
        db.rollback()


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


def test_duplicate_worker_contention_does_not_mutate_queued_run(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-sync-contention@example.com")
    owner_id = str(owner.id)
    day = date(2026, 10, 5)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(owner_id),
            OzonSyncRun(
                user_id=owner_id,
                date_from=day,
                date_to=day,
                status="queued",
                progress=0,
                error_message=None,
            ),
        ]
    )
    real_db_session.commit()
    run = real_db_session.query(OzonSyncRun).filter_by(user_id=owner_id).one()
    bind = real_db_session.get_bind()
    holder = bind.engine.connect()
    lock_key = _lock_key(owner_id)
    try:
        holder.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
        holder.commit()

        result = run_sync_run(db=real_db_session, run_id=str(run.id))

        real_db_session.refresh(run)
        assert result == {"status": "busy", "current_status": "queued"}
        assert run.status == "queued"
        assert run.error_message is None
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})
        holder.commit()
        holder.close()


def test_live_owner_lock_prevents_stale_run_replacement(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-sync-stale-locked@example.com")
    owner_id = str(owner.id)
    day = date(2026, 10, 4)
    stale_updated_at = datetime.now(UTC) - timedelta(hours=4)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all(
        [
            _connection(owner_id),
            OzonSyncRun(
                user_id=owner_id,
                date_from=day,
                date_to=day,
                status="running",
                progress=0,
                updated_at=stale_updated_at,
            ),
        ]
    )
    real_db_session.commit()
    existing = real_db_session.query(OzonSyncRun).filter_by(user_id=owner_id).one()
    bind = real_db_session.get_bind()
    holder = bind.engine.connect()
    lock_key = _lock_key(owner_id)
    try:
        holder.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
        holder.commit()

        with pytest.raises(MarketplaceFinanceError, match="Синхронизация уже ожидает запуска или выполняется"):
            enqueue_sync(db=real_db_session, user_id=owner_id, date_from=day, date_to=day)

        real_db_session.refresh(existing)
        assert existing.status == "running"
        assert existing.updated_at == stale_updated_at
        assert real_db_session.query(OzonSyncRun).filter_by(user_id=owner_id).count() == 1
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})
        holder.commit()
        holder.close()


def test_missing_day_keeps_specific_refresh_failure_visible(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("marketplace-missing-error@example.com")
    day = date(2026, 10, 10)
    real_db_session.add(owner)
    real_db_session.commit()
    real_db_session.add_all([
        _connection(str(owner.id)),
        OzonSyncDay(user_id=str(owner.id), day=day, status="missing",
                    last_attempt_status="failed", warnings=[],
                    last_error="Ozon ограничил частоту запросов"),
    ])
    real_db_session.commit()
    result = build_overview(db=real_db_session, store_owner=owner,
                            marketplace="ozon", date_from=day, date_to=day)
    assert result["coverage"]["complete"] is False
    assert result["daily"][0]["revenue"] is None
    assert any("2026-10-10: день не загружен из-за ошибки: Ozon ограничил" in w
               for w in result["warnings"])
    assert not any("последний сохранённый срез" in w for w in result["warnings"])
