from unittest.mock import patch

from app.models.article import Article
from app.models.user import User
from celery_app.tasks import sync_funnel


def test_sync_funnel_sets_article_name_from_wb_title(real_db_session):
    u = User(
        id="b0000000-0000-4000-8000-000000000010",
        email="funnel-title@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(u)
    real_db_session.commit()

    # Article exists but without name/vendor_code.
    real_db_session.add(Article(user_id=str(u.id), nm_id=123))
    real_db_session.commit()

    wb_rows = [
        {
            "date": "2026-04-01",
            "nm_id": 123,
            "vendor_code": "VC-123",
            "title": "Тестовый товар",
            "open_count": 1,
            "cart_count": 0,
            "order_count": 0,
            "order_sum": 0,
            "buyout_percent": None,
            "cr_to_cart": None,
            "cr_to_order": None,
            "subject_name": "Кроссовки",
        }
    ]

    with patch("celery_app.tasks.SessionLocal", return_value=real_db_session):
        with patch("celery_app.tasks.fetch_funnel", return_value=wb_rows):
            res = sync_funnel(str(u.id), "2026-04-01", "2026-04-01")

    assert res.get("ok") is True
    art = (
        real_db_session.query(Article)
        .filter(Article.user_id == str(u.id), Article.nm_id == 123)
        .first()
    )
    assert art is not None
    assert (art.name or "").strip() == "Тестовый товар"

