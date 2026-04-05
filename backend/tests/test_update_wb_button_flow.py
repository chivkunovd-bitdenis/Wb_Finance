"""
Интерактивный интеграционный тест сценария кнопки «ОБНОВИТЬ WB».

Задача: поймать проблему "нажал — а витрина как будто не изменилась",
которая появляется из-за некорректной последовательности ожидания асинхронных sync/recalculate.

Что делаем:
1) Пишем "до" (GET /dashboard/pnl) и фиксируем signature.
2) Эмулируем кнопку: POST /sync/sales + POST /sync/ads (но .delay() для sync НЕ выполняет задачи сразу).
3) Сразу делаем GET /dashboard/pnl и проверяем, что signature не изменился (как "ничего не работает").
4) Затем симулируем завершение воркера: вручную вызываем sync_sales/sync_ads (они уже триггерят recalculate_* через delay()).
5) В конце повторяем GET /dashboard/pnl и убеждаемся, что signature изменился.

Тесты полностью детерминированы: WB клиент подменён, реальная БД используется в рамках rollback.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db
from app.models.article import Article
from app.models.user import User
from app.core.security import create_access_token
from celery_app.tasks import sync_sales, sync_ads, recalculate_pnl, recalculate_sku_daily



def signature_of_pnl(rows: list[dict]) -> str:
    lst = rows or []
    total_revenue = sum((float(r.get("revenue") or 0) for r in lst))
    last = lst[-1] if lst else {}
    return str(
        {
            "len": len(lst),
            "totalRevenue": round(total_revenue * 100) / 100,
            "lastDate": last.get("date") or None,
            "lastRev": round(float(last.get("revenue") or 0) * 100) / 100,
        }
    )


@pytest.fixture
def authenticated_client_with_session(real_db_session):
    """Тестовый клиент, где и API, и Celery tasks читают/пишут в ОДНУ и ту же session."""
    user = User(
        id="a0000000-0000-4000-8000-000000000002",
        email="btnflow@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    token = create_access_token(data={"sub": str(user.id)})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        # task'ы вызывают db.close() — в тесте не хотим закрывать общую session
        real_db_session.close = MagicMock()
        with TestClient(app) as c:
            yield c, str(user.id), token, real_db_session
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def wb_fixed_rows():
    """Фиксированные данные WB для sync_sales/sync_ads (без сети)."""
    date_from = "2025-03-20"
    date_to = "2025-03-20"
    nm_id = 999

    sales = [
        {
            "date": date_from,
            "nm_id": nm_id,
            "doc_type": "Продажа",
            "retail_price": 1000,
            "ppvz_for_pay": 850,
            "delivery_rub": 50,
            "penalty": 0,
            "additional_payment": 0,
            "storage_fee": 10,
            "quantity": 1,
        }
    ]

    ads = [
        {
            "date": date_from,
            "nm_id": nm_id,
            "campaign_id": 123,
            "spend": 200,
        }
    ]

    return date_from, date_to, nm_id, sales, ads


def _get_pnl_sig(client: TestClient, token: str, date_from: str, date_to: str) -> str:
    r = client.get(
        "/dashboard/pnl",
        params={"date_from": date_from, "date_to": date_to},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    return signature_of_pnl(r.json())


@pytest.mark.usefixtures("real_db_session")
def test_update_wb_polling_waits_until_pnl_changes(authenticated_client_with_session, wb_fixed_rows):
    """
    Моделируем новый сценарий кнопки:
    - sync_sales/sync_ads поставлены, но не выполнены немедленно
    - сразу GET /dashboard/pnl показывает "как было" (это и есть "ничего не работает")
    - затем симулируем завершение sync задач
    - витрина меняется
    """
    client, user_id, token, session = authenticated_client_with_session
    date_from, date_to, nm_id, sales_rows, ads_rows = wb_fixed_rows

    # себестоимость, чтобы recalculate_pnl не делал cogs=0 (не критично для signature, но корректнее)
    session.add(Article(user_id=user_id, nm_id=nm_id, cost_price=100))
    session.commit()

    before_sig = _get_pnl_sig(client, token, date_from, date_to)

    # sync.delay из API не выполняем сразу (эмулируем async queue).
    with (
        patch("celery_app.tasks.fetch_sales", return_value=sales_rows),
        patch("celery_app.tasks.fetch_ads", return_value=ads_rows),
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch.object(sync_sales, "delay", return_value=MagicMock(id="sync-sales-queued")),
        patch.object(sync_ads, "delay", return_value=MagicMock(id="sync-ads-queued")),
        # recalculate.delay выполняем синхронно (чтобы, когда sync_sales/sync_ads закончатся,
        # витрина обновилась сразу внутри теста).
        patch.object(recalculate_pnl, "delay", side_effect=lambda *args, **kwargs: recalculate_pnl(*args, **kwargs)),
        patch.object(recalculate_sku_daily, "delay", side_effect=lambda *args, **kwargs: recalculate_sku_daily(*args, **kwargs)),
    ):
        # "Кнопка": ставим async задачи, но sync ещё не выполнен
        r1 = client.post(
            "/sync/sales",
            json={"date_from": date_from, "date_to": date_to},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/sync/ads",
            json={"date_from": date_from, "date_to": date_to},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200

        # Сразу после нажатия: витрина не должна измениться (sync не исполнялся)
        immediate_sig = _get_pnl_sig(client, token, date_from, date_to)
        assert immediate_sig == before_sig

        # Симулируем, что воркер начал выполнение задач и sync завершился
        sync_sales(user_id, date_from, date_to)
        sync_ads(user_id, date_from, date_to)

        # "Poll": витрина должна измениться
        after_sig = _get_pnl_sig(client, token, date_from, date_to)
        assert after_sig != before_sig


def test_update_wb_old_sequence_single_refresh_is_wrong(authenticated_client_with_session, wb_fixed_rows):
    """
    Моделируем прежнюю "ошибочную" логику:
    - ставим sync_sales/sync_ads, но sync ещё не выполнен
    - тут же вызываем /sync/recalculate (recalculate запускается на старых raw)
    - один refresh сразу после этого даёт "ничего не изменилось"
    - после реального завершения sync signature изменится
    """
    client, user_id, token, session = authenticated_client_with_session
    date_from, date_to, nm_id, sales_rows, ads_rows = wb_fixed_rows

    session.add(Article(user_id=user_id, nm_id=nm_id, cost_price=100))
    session.commit()

    before_sig = _get_pnl_sig(client, token, date_from, date_to)

    with (
        patch("celery_app.tasks.fetch_sales", return_value=sales_rows),
        patch("celery_app.tasks.fetch_ads", return_value=ads_rows),
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch.object(sync_sales, "delay", return_value=MagicMock(id="sync-sales-queued")),
        patch.object(sync_ads, "delay", return_value=MagicMock(id="sync-ads-queued")),
        patch.object(recalculate_pnl, "delay", side_effect=lambda *args, **kwargs: recalculate_pnl(*args, **kwargs)),
        patch.object(recalculate_sku_daily, "delay", side_effect=lambda *args, **kwargs: recalculate_sku_daily(*args, **kwargs)),
    ):
        # async sync задачи не выполняются
        r1 = client.post(
            "/sync/sales",
            json={"date_from": date_from, "date_to": date_to},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/sync/ads",
            json={"date_from": date_from, "date_to": date_to},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200

        # Симулируем "старую" логику: пересчёт выполнен до того,
        # как sync успел записать raw_sales/raw_ads (raw таблицы пустые).
        recalculate_pnl(user_id, date_from, date_to)
        recalculate_sku_daily(user_id, date_from, date_to)

        # "Один refresh" сразу после пересчёта (ещё до sync) — изменений нет
        after_old_one_refresh_sig = _get_pnl_sig(client, token, date_from, date_to)
        assert after_old_one_refresh_sig == before_sig

        # Теперь sync реально исполним
        sync_sales(user_id, date_from, date_to)
        sync_ads(user_id, date_from, date_to)

        final_sig = _get_pnl_sig(client, token, date_from, date_to)
        assert final_sig != before_sig

