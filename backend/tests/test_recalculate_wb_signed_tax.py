"""Stored WB days move to the signed tax: only tax and margin of old-rule rows change (real database)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User
from scripts.recalculate_wb_signed_tax import (
    owners_with_old_rule_rows,
    recalculate_owner,
)

COLUMNS = ("revenue", "commission", "logistics", "penalties", "storage", "ads_spend", "cogs", "tax", "margin")


def _user(session, email: str) -> str:
    user = User(id=str(uuid4()), email=email, password_hash="hash", is_active=True, tax_rate=Decimal("0.06"))
    session.add(user)
    session.commit()
    return str(user.id)


def _pnl(user_id: str, day: date, revenue: str, tax: str, margin: str) -> PnlDaily:
    return PnlDaily(
        user_id=user_id,
        date=day,
        revenue=Decimal(revenue),
        commission=Decimal("-150.00"),
        logistics=Decimal("50.00"),
        penalties=Decimal(0),
        storage=Decimal("3.00"),
        ads_spend=Decimal("7.00"),
        cogs=Decimal("-100.00"),
        tax=Decimal(tax),
        operation_expenses=Decimal("25.00"),
        margin=Decimal(margin),
    )


def _snapshot(row) -> dict[str, Decimal | None]:
    return {column: getattr(row, column) for column in COLUMNS}


def _seed(session):
    owner = _user(session, f"wb-signed-tax-{uuid4().hex[:8]}@example.com")
    other = _user(session, f"wb-signed-tax-other-{uuid4().hex[:8]}@example.com")
    rows = {
        "old_return_day": _pnl(owner, date(2026, 3, 1), "-1000.00", "0", "-835.00"),
        "sale_day": _pnl(owner, date(2026, 3, 2), "2000.00", "120.00", "1500.00"),
        "already_signed": _pnl(owner, date(2026, 3, 3), "-200.00", "-12.00", "-50.00"),
        "below_a_kopeck": _pnl(owner, date(2026, 3, 4), "-0.05", "0", "-10.00"),
        "other_owner": _pnl(other, date(2026, 3, 1), "-500.00", "0", "-400.00"),
        "sku_old_return": SkuDaily(
            user_id=owner,
            date=date(2026, 3, 1),
            nm_id=999,
            revenue=Decimal("-1000.00"),
            commission=Decimal("-150.00"),
            logistics=Decimal("50.00"),
            penalties=Decimal(0),
            storage=Decimal(0),
            ads_spend=Decimal(0),
            cogs=Decimal("-100.00"),
            tax=Decimal(0),
            margin=Decimal("-800.00"),
            order_count=4,
        ),
    }
    session.add_all(rows.values())
    session.commit()
    return owner, other, rows


def test_dry_run_counts_old_rule_rows_and_writes_nothing(real_db_session):
    owner, other, rows = _seed(real_db_session)
    before = {name: _snapshot(row) for name, row in rows.items()}

    assert {owner, other} <= set(owners_with_old_rule_rows(real_db_session))
    result = recalculate_owner(real_db_session, owner, apply=False)

    assert result.tables["pnl_daily"].rows == 1
    assert result.tables["pnl_daily"].tax_delta == Decimal("-60.00")
    assert result.tables["sku_daily"].rows == 1
    for name, row in rows.items():
        real_db_session.refresh(row)
        assert _snapshot(row) == before[name]


def test_apply_changes_only_tax_and_margin_of_old_rule_rows_and_is_idempotent(real_db_session):
    owner, _other, rows = _seed(real_db_session)
    before = {name: _snapshot(row) for name, row in rows.items()}

    recalculate_owner(real_db_session, owner, apply=True)
    for row in rows.values():
        real_db_session.refresh(row)

    old = rows["old_return_day"]
    assert old.tax == Decimal("-60.00")  # 6% of −1000, signed
    assert old.margin == Decimal("-775.00")  # −835 + 60: the margin moves by exactly the tax change
    sku = rows["sku_old_return"]
    assert sku.tax == Decimal("-60.00")
    assert sku.margin == Decimal("-740.00")
    assert sku.order_count == 4
    for name in ("old_return_day", "sku_old_return"):
        changed = {column for column in COLUMNS if getattr(rows[name], column) != before[name][column]}
        assert changed == {"tax", "margin"}
    for name in ("sale_day", "already_signed", "below_a_kopeck", "other_owner"):
        assert _snapshot(rows[name]) == before[name]

    again = recalculate_owner(real_db_session, owner, apply=True)
    assert again.tables["pnl_daily"].rows == 0
    assert again.tables["sku_daily"].rows == 0
    real_db_session.refresh(old)
    assert old.margin == Decimal("-775.00")
