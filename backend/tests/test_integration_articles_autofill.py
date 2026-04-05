"""
Интеграционный тест: после sync_sales/sync_ads в БД появляются записи в articles.

Это нужно, чтобы вкладка «Себестоимость» не была пустой сразу после первого входа.
"""

from unittest.mock import MagicMock, patch

from app.models.article import Article
from app.models.user import User
from celery_app.tasks import sync_sales, sync_ads


def test_sync_sales_autofills_articles(real_db_session):
    user = User(
        id="b0000000-0000-4000-8000-000000000001",
        email="auto-articles@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()

    wb_rows = [
        {"date": "2026-03-01", "nm_id": 111, "doc_type": "Продажа", "retail_price": 100, "ppvz_for_pay": 90, "delivery_rub": 0, "penalty": 0, "additional_payment": 0, "storage_fee": 0, "quantity": 1},
        {"date": "2026-03-02", "nm_id": 222, "doc_type": "Продажа", "retail_price": 200, "ppvz_for_pay": 180, "delivery_rub": 0, "penalty": 0, "additional_payment": 0, "storage_fee": 0, "quantity": 1},
    ]

    # чтобы задача не закрыла сессию теста
    real_db_session.close = MagicMock()

    with patch("celery_app.tasks.fetch_sales", return_value=wb_rows):
        with patch("celery_app.tasks.SessionLocal", return_value=real_db_session):
            with patch("celery_app.tasks.recalculate_pnl") as mock_rec1, patch("celery_app.tasks.recalculate_sku_daily") as mock_rec2:
                mock_rec1.delay.return_value = MagicMock(id="rec-pnl")
                mock_rec2.delay.return_value = MagicMock(id="rec-sku")
                res = sync_sales(str(user.id), "2026-03-01", "2026-03-02")

    assert res["ok"] is True
    arts = real_db_session.query(Article).filter(Article.user_id == str(user.id)).all()
    nm_ids = sorted([a.nm_id for a in arts])
    assert nm_ids == [111, 222]


def test_sync_ads_autofills_articles(real_db_session):
    user = User(
        id="b0000000-0000-4000-8000-000000000002",
        email="auto-articles-ads@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()

    wb_rows = [
        {"date": "2026-03-01", "nm_id": 333, "campaign_id": 1, "spend": 10},
        {"date": "2026-03-02", "nm_id": 333, "campaign_id": 1, "spend": 15},
        {"date": "2026-03-02", "nm_id": 444, "campaign_id": 2, "spend": 5},
    ]

    real_db_session.close = MagicMock()

    with patch("celery_app.tasks.fetch_ads", return_value=wb_rows):
        with patch("celery_app.tasks.SessionLocal", return_value=real_db_session):
            with patch("celery_app.tasks.recalculate_pnl") as mock_rec1, patch("celery_app.tasks.recalculate_sku_daily") as mock_rec2:
                mock_rec1.delay.return_value = MagicMock(id="rec-pnl")
                mock_rec2.delay.return_value = MagicMock(id="rec-sku")
                res = sync_ads(str(user.id), "2026-03-01", "2026-03-02")

    assert res["ok"] is True
    arts = real_db_session.query(Article).filter(Article.user_id == str(user.id)).all()
    nm_ids = sorted([a.nm_id for a in arts])
    assert nm_ids == [333, 444]

