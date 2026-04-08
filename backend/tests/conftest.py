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
from app.db import DATABASE_URL as APP_DATABASE_URL
from app.models.base import Base

# Импорт моделей, чтобы Base.metadata знал все таблицы
from app.models import User, Article, RawSale, RawAd, PnlDaily, FunnelDaily, SkuDaily, OperationalExpense, Subscription, Payment, License, ReminderLog  # noqa: F401


@pytest.fixture(scope="session", autouse=True)
def _celery_test_mode():
    """
    Тестовый режим Celery: никаких попыток подключиться к redis://redis:6379/0.

    В проде/докере Celery настроен на redis (см. celery_app/celery.py). Для локального запуска тестов
    без docker-сети это имя хоста недоступно, поэтому включаем eager+memory backend.
    """
    from celery_app.celery import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_ignore_result = True
    celery_app.conf.broker_url = "memory://"
    celery_app.conf.result_backend = "cache+memory://"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """
    Реальные тесты WB помечены marker'ом wb_real:
    - требуют ключ WB_API_KEY
    - зависят от внешней сети и "живых" данных

    Политика: если WB_API_KEY задан — запускаем wb_real тесты автоматически.
    Если WB_API_KEY не задан — пропускаем wb_real (иначе их невозможно выполнить).
    """
    has_key = bool((os.getenv("WB_API_KEY") or "").strip())
    if has_key:
        return
    reason = "wb_real пропущены (нужно задать WB_API_KEY)"
    for item in items:
        if item.get_closest_marker("wb_real"):
            item.add_marker(pytest.mark.skip(reason=reason))


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
    """
    URL для интеграционных тестов:
    - если DATABASE_URL задан, используем его (например, в docker compose run)
    - иначе берём дефолт из приложения (localhost), чтобы тесты могли запускаться
      при поднятом локальном postgres без ручной прокидки env.
    """
    url = (os.getenv("DATABASE_URL") or "").strip() or (APP_DATABASE_URL or "").strip()
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
    # Гарантируем наличие таблиц для интеграционных тестов.
    # (Миграции в этом фикстуре не гоняем; если схема несовместима — тесты всё равно упадут и это сигнал.)
    Base.metadata.create_all(bind=conn)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=conn)
    session = Session()
    session.begin_nested()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
