"""
Unit-тесты Celery-задач: recalculate_pnl (pnl_daily), recalculate_sku_daily (sku_daily).
БД подменена на mock — проверяем, что задачи не падают и возвращают ожидаемый формат.
"""
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from datetime import date


from celery_app.tasks import _build_desc_days_batch, _build_desc_month_chunk, recalculate_pnl, recalculate_sku_daily


def _make_mock_db_empty():
    """Мок-сессия: пользователь есть, сырых данных за период нет → витрина пустая (count=0)."""
    db = MagicMock()
    db.get.return_value = MagicMock(id="user-1")
    # query(Model).filter(...).all() для Article; for r in query(Model).filter(...) для RawSale/RawAd/FunnelDaily
    filter_result = MagicMock()
    filter_result.all.return_value = []
    filter_result.__iter__ = lambda self: iter([])
    db.query.return_value.filter.return_value = filter_result
    db.execute.return_value = None
    db.add.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db.close.return_value = None
    return db


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_sku_daily_empty_period_returns_ok_and_count_zero(mock_session_local):
    """При пустых raw_sales/raw_ads/funnel за период задача возвращает ok=True, count=0."""
    mock_session_local.return_value = _make_mock_db_empty()
    result = recalculate_sku_daily("user-1", "2025-03-01", "2025-03-05")
    assert result == {"ok": True, "count": 0}
    mock_session_local.return_value.commit.assert_called_once()
    mock_session_local.return_value.close.assert_called_once()


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_sku_daily_ignores_nm_id_zero_rows(mock_session_local):
    """
    Регрессия: в raw_sales иногда попадают строки без nm_id и сохраняются как nm_id=0.
    Такие строки не должны создавать псевдо-артикул 0 в sku_daily и портить вкладку "Артикулы".
    """
    user_id = "user-1"
    raw_sale_row = SimpleNamespace(
        nm_id=0,
        quantity=1,
        retail_price=0,
        ppvz_for_pay=0,
        doc_type="Продажа",
        delivery_rub=483.0,
        penalty=0,
        additional_payment=0,
        storage_fee=0,
        date=date(2025, 3, 20),
    )

    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=user_id, tax_rate=0.06)

    def _query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "Article":
            q.filter.return_value.all.return_value = []
            return q
        if model.__name__ == "RawSale":
            q.filter.return_value = [raw_sale_row]
            return q
        if model.__name__ == "RawAd":
            q.filter.return_value = []
            return q
        if model.__name__ == "FunnelDaily":
            q.filter.return_value = []
            return q
        return q

    db.query.side_effect = _query_side_effect
    db.execute.return_value = None
    db.add.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db.close.return_value = None

    mock_session_local.return_value = db

    result = recalculate_sku_daily(user_id, "2025-03-20", "2025-03-20")
    assert result == {"ok": True, "count": 0}
    assert db.add.call_count == 0


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_pnl_includes_nm_id_zero_rows_in_storage_and_margin(mock_session_local):
    """
    Важно для общего P&L: строки WB без nm_id (nm_id=0) должны влиять на storage/logistics/penalties.
    Они не должны попадать в витрину по артикулам (sku_daily), но в pnl_daily учитываются.
    """
    user_id = "user-1"
    raw_sale_row = SimpleNamespace(
        nm_id=0,
        quantity=1,
        retail_price=0,
        ppvz_for_pay=0,
        doc_type="Прочее",
        delivery_rub=0,
        penalty=0,
        additional_payment=0,
        storage_fee=388.0,
        date=date(2025, 3, 20),
    )

    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=user_id, tax_rate=0.06)

    def _query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "Article":
            q.filter.return_value.all.return_value = []
            return q
        if model.__name__ == "OperationalExpense":
            q.filter.return_value.all.return_value = []
            return q
        if model.__name__ == "RawSale":
            q.filter.return_value = [raw_sale_row]
            return q
        if model.__name__ == "RawAd":
            q.filter.return_value = []
            return q
        return q

    db.query.side_effect = _query_side_effect
    db.execute.return_value = None
    db.add.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db.close.return_value = None

    mock_session_local.return_value = db

    result = recalculate_pnl(user_id, "2025-03-20", "2025-03-20")
    assert result == {"ok": True, "count": 1}

    assert db.add.call_count == 1
    added_row = db.add.call_args_list[0][0][0]
    assert float(added_row.storage) == 388.0
    assert float(added_row.margin) == -388.0


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_sku_daily_user_not_found_returns_error(mock_session_local):
    """При отсутствии пользователя задача возвращает ok=False, error=user_not_found, сессия закрывается."""
    db = MagicMock()
    db.get.return_value = None
    db.close.return_value = None
    mock_session_local.return_value = db

    result = recalculate_sku_daily("nonexistent", "2025-03-01", "2025-03-05")

    assert result == {"ok": False, "error": "user_not_found"}
    db.close.assert_called_once()


# --- recalculate_pnl ---


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_pnl_empty_period_returns_ok_and_count_zero(mock_session_local):
    """При пустых raw_sales/raw_ads за период задача возвращает ok=True, count=0."""
    mock_session_local.return_value = _make_mock_db_empty()
    result = recalculate_pnl("user-1", "2025-03-01", "2025-03-05")
    assert result == {"ok": True, "count": 0}
    mock_session_local.return_value.commit.assert_called_once()
    mock_session_local.return_value.close.assert_called_once()


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_pnl_user_not_found_returns_error(mock_session_local):
    """При отсутствии пользователя задача возвращает ok=False, error=user_not_found."""
    db = MagicMock()
    db.get.return_value = None
    db.close.return_value = None
    mock_session_local.return_value = db

    result = recalculate_pnl("nonexistent", "2025-03-01", "2025-03-05")

    assert result == {"ok": False, "error": "user_not_found"}
    db.close.assert_called_once()


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_pnl_subtracts_operational_expenses(mock_session_local):
    """Маржа должна уменьшаться на операционные расходы по дню."""

    user_id = "user-1"
    raw_sale_row = SimpleNamespace(
        nm_id=999,
        quantity=1,
        retail_price=1000,
        ppvz_for_pay=850,
        doc_type="Продажа",
        delivery_rub=50,
        penalty=0,
        additional_payment=0,
        storage_fee=10,
        date=date(2025, 3, 20),
    )
    raw_ads_rows = []

    article_row = SimpleNamespace(nm_id=999, cost_price=100)
    op_exp_row = SimpleNamespace(date=date(2025, 3, 20), amount=25.5)

    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=user_id, tax_rate=0.06)

    def _query_side_effect(model):
        q = MagicMock()

        if model.__name__ == "Article":
            q.filter.return_value.all.return_value = [article_row]
            return q
        if model.__name__ == "OperationalExpense":
            q.filter.return_value.all.return_value = [op_exp_row]
            return q
        if model.__name__ == "RawSale":
            q.filter.return_value = [raw_sale_row]
            return q
        if model.__name__ == "RawAd":
            q.filter.return_value = raw_ads_rows
            return q

        return q

    db.query.side_effect = _query_side_effect
    db.execute.return_value = None
    db.add.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db.close.return_value = None

    mock_session_local.return_value = db

    result = recalculate_pnl(user_id, "2025-03-20", "2025-03-20")
    assert result == {"ok": True, "count": 1}

    # Ловим PnlDaily, которые добавили в витрину.
    assert db.add.call_count == 1
    added_row = db.add.call_args_list[0][0][0]
    assert float(added_row.operation_expenses) == 25.5
    assert float(added_row.margin) == 604.5


@patch("celery_app.tasks.SessionLocal")
def test_recalculate_pnl_only_operational_expenses_creates_pnl_daily(mock_session_local):
    """
    Если в период есть только OperationalExpense (без RawSale и RawAd),
    recalculate_pnl всё равно должен создать запись pnl_daily за дату.
    """
    user_id = "user-1"
    op_date = date(2025, 3, 20)
    op_amount = 25.5
    op_exp_row = SimpleNamespace(date=op_date, amount=op_amount)

    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=user_id, tax_rate=0.06)

    def _query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "Article":
            q.filter.return_value.all.return_value = []
            return q
        if model.__name__ == "OperationalExpense":
            q.filter.return_value.all.return_value = [op_exp_row]
            return q
        if model.__name__ == "RawSale":
            q.filter.return_value = []
            return q
        if model.__name__ == "RawAd":
            q.filter.return_value = []
            return q
        return q

    db.query.side_effect = _query_side_effect
    db.execute.return_value = None
    db.add.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db.close.return_value = None

    mock_session_local.return_value = db

    result = recalculate_pnl(user_id, op_date.isoformat(), op_date.isoformat())
    assert result == {"ok": True, "count": 1}

    assert db.add.call_count == 1
    added_row = db.add.call_args_list[0][0][0]
    assert float(added_row.operation_expenses) == op_amount
    assert float(added_row.margin) == -op_amount


def test_build_desc_days_batch_returns_reverse_order_with_limit():
    days = _build_desc_days_batch(
        cursor=date(2026, 3, 20),
        year_start=date(2026, 1, 1),
        limit=3,
    )
    assert days == [date(2026, 3, 20), date(2026, 3, 19), date(2026, 3, 18)]


def test_build_desc_days_batch_stops_at_year_start():
    days = _build_desc_days_batch(
        cursor=date(2026, 1, 2),
        year_start=date(2026, 1, 1),
        limit=5,
    )
    assert days == [date(2026, 1, 2), date(2026, 1, 1)]


def test_build_desc_month_chunk_returns_month_to_cursor():
    start, end = _build_desc_month_chunk(cursor=date(2026, 3, 26), year_start=date(2026, 1, 1))
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 26)


def test_build_desc_month_chunk_caps_to_year_start():
    start, end = _build_desc_month_chunk(cursor=date(2026, 1, 5), year_start=date(2026, 1, 3))
    assert start == date(2026, 1, 3)
    assert end == date(2026, 1, 5)
