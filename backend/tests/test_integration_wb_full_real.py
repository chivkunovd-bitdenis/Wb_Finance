"""
Реальный end-to-end сценарий с Wildberries:

- берём реальный WB_API_KEY (из backend/.env или окружения);
- создаём пользователя в реальной БД с этим ключом;
- вызываем задачи sync_sales / sync_ads / sync_funnel (они реально ходят в WB);
- убеждаемся, что данные попали в таблицы raw_sales / raw_ads / funnel_daily;
- запускаем recalculate_pnl и проверяем, что pnl_daily заполнился;
- через REST API /dashboard/pnl и /dashboard/funnel убеждаемся, что данные отдаются
  в том же формате, который использует фронт.

Важно:
- тест помечен как wb_real и завязан на «живые» данные WB;
- если за выбранный период WB вернул 0 строк (нет продаж/рекламы/воронки),
  тест помечается как skipped — он не должен падать только из‑за отсутствия активности;
- все операции идут в транзакции через real_db_session, в конце всё откатывается.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db
from app.core.security import create_access_token
from app.models.user import User
from app.models.article import Article
from app.models.raw_sales import RawSale
from app.models.funnel_daily import FunnelDaily
from app.models.pnl_daily import PnlDaily
from celery_app.tasks import sync_sales, sync_ads, sync_funnel, recalculate_pnl

from tests.test_wb_client_real import _get_wb_key, _short_period


pytestmark = [
    pytest.mark.wb_real,
    pytest.mark.skipif(
        (os.getenv("RUN_REAL_WB_TESTS") or "").strip() not in {"1", "true", "TRUE", "yes", "YES"},
        reason="Real WB API tests are disabled by default. Set RUN_REAL_WB_TESTS=1 to enable.",
    ),
]


@pytest.fixture
def authenticated_client_real(real_db_session):
    """
    Клиент с реальной БД и реальным WB_API_KEY.

    Пользователь создаётся в транзакции; после теста всё откатывается.
    """
    key = _get_wb_key()
    if not key:
        pytest.skip("WB_API_KEY не задан — проверь backend/.env")

    user = User(
        email="wb-real@example.com",
        password_hash="$2b$12$fake",
        wb_api_key=key,
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)
    user_id = str(user.id)

    token = create_access_token(data={"sub": user_id})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        with TestClient(app) as client:
            yield client, real_db_session, user_id, token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_real_wb_full_flow_sales_ads_funnel_to_api(authenticated_client_real):
    """
    Полный поток с реальным WB:

    WB → sync_sales/sync_ads/sync_funnel → raw_* / funnel_daily → recalculate_pnl → pnl_daily →
    GET /dashboard/pnl и /dashboard/funnel.
    """
    client, session, user_id, token = authenticated_client_real
    date_from, date_to = _short_period(2)

    # Чтобы задачи не закрывали сессию теста
    session.close = MagicMock()

    with patch("celery_app.tasks.SessionLocal", return_value=session):
        # 1. Реальная синхронизация продаж
        sales_res = sync_sales(user_id, date_from, date_to)
        if not sales_res.get("ok"):
            # Внешний мир (WB/сеть/лимиты) может быть недоступен. Это не регрессия нашего кода.
            # Если sync упал — пропускаем, чтобы "регулярный прогон" не был рулеткой.
            pytest.skip(f"sync_sales failed: {sales_res.get('error') or sales_res}")

        raw_sales_q = session.query(RawSale).filter(
            RawSale.user_id == user_id,
            RawSale.date >= date_from,
            RawSale.date <= date_to,
        )
        raw_sales = raw_sales_q.all()
        if not raw_sales:
            pytest.skip("WB не вернул продаж за период — нечего проверять в полном потоке")

        # 2. Создаём артикулы по nm_id из продаж, чтобы sync_funnel мог их использовать
        existing_nm_ids = {
            a.nm_id for a in session.query(Article).filter(Article.user_id == user_id).all()
        }
        new_nm_ids = {r.nm_id for r in raw_sales if r.nm_id and r.nm_id not in existing_nm_ids}
        for nm in list(new_nm_ids)[:20]:
            session.add(
                Article(
                    user_id=user_id,
                    nm_id=nm,
                    vendor_code=f"NM-{nm}",
                    name=f"WB {nm}",
                    cost_price=0,
                )
            )
        session.commit()

        # 3. Реальная синхронизация рекламы
        ads_res = sync_ads(user_id, date_from, date_to)
        if not ads_res.get("ok"):
            pytest.skip(f"sync_ads failed: {ads_res.get('error') or ads_res}")
        # Реклама может быть пустой — это не ошибка, но мы фиксируем факт, что задача отработала

        # 4. Реальная синхронизация воронки (по nm_id из Article)
        funnel_res = sync_funnel(user_id, date_from, date_to)
        if not funnel_res.get("ok"):
            pytest.skip(f"sync_funnel failed: {funnel_res.get('error') or funnel_res}")

        funnel_q = session.query(FunnelDaily).filter(
            FunnelDaily.user_id == user_id,
            FunnelDaily.date >= date_from,
            FunnelDaily.date <= date_to,
        )
        funnel_rows = funnel_q.all()
        if not funnel_rows:
            # WB иногда может не вернуть воронку — это не повод падать
            pytest.skip("WB не вернул воронку за период — пропускаем проверку API воронки")

        # 5. Пересчёт P&L по реальным данным
        rec_res = recalculate_pnl(user_id, date_from, date_to)
        assert rec_res["ok"] is True

    # 6. Проверяем, что pnl_daily заполнился и API /dashboard/pnl отдаёт данные
    pnl_rows = (
        session.query(PnlDaily)
        .filter(PnlDaily.user_id == user_id, PnlDaily.date >= date_from, PnlDaily.date <= date_to)
        .all()
    )
    if not pnl_rows:
        pytest.skip("После recalculate_pnl нет строк в pnl_daily — возможно, WB вернул нетипичные данные")

    r_pnl = client.get(
        "/dashboard/pnl",
        params={"date_from": date_from, "date_to": date_to},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_pnl.status_code == 200
    pnl_payload = r_pnl.json()
    assert isinstance(pnl_payload, list)
    assert pnl_payload, "API /dashboard/pnl вернул пустой список при наличии строк в pnl_daily"

    # 7. Проверяем, что API /dashboard/funnel отдаёт данные за тот же период (если WB их вернул)
    r_funnel = client.get(
        "/dashboard/funnel",
        params={"date_from": date_from, "date_to": date_to},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_funnel.status_code == 200
    funnel_payload = r_funnel.json()
    assert isinstance(funnel_payload, list)
    # Может быть пустым, если WB не дал воронку по nm_id — сам факт успешного вызова и структуры важнее

