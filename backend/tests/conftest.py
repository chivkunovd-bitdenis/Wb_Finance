"""
Общие фикстуры для тестов.
При запуске через docker compose run контейнеру передаётся DATABASE_URL — парсинг не падает.
Тесты auth/sync подменяют get_db на mock, в реальную БД не пишем.
Интеграционные тесты (test_integration_*) используют real_db_session и реальную БД в транзакции с rollback.
"""
import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app

# Импорт моделей, чтобы Base.metadata знал все таблицы
from app.models import User, Article, RawSale, RawAd, PnlDaily, FunnelDaily, SkuDaily, OperationalExpense, Subscription, Payment, License, ReminderLog  # noqa: F401


@pytest.fixture
def client():
    """Клиент для запросов к API без реальной БД."""
    return TestClient(app)


@pytest.fixture
def mock_db_session():
    """Подмена сессии БД: запросы не идут в PostgreSQL."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.get.return_value = None
    return session


def _get_engine():
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return None
    return create_engine(url, pool_pre_ping=True)


@pytest.fixture(scope="function")
def real_db_session():
    """
    Сессия к реальной БД в транзакции с откатом.
    Все изменения после теста откатываются. Нужна работающая PostgreSQL (docker compose up).
    """
    engine = _get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL не задан — интеграционные тесты пропущены")
    try:
        conn = engine.connect()
    except Exception as e:
        pytest.skip(f"Не удалось подключиться к БД: {e}")
    trans = conn.begin()
    Session = sessionmaker(autocommit=False, autoflush=False, bind=conn)
    session = Session()
    session.begin_nested()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
