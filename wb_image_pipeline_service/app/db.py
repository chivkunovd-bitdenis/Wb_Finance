from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base
import app.models.pipeline  # noqa: F401 — таблицы в metadata

logger = logging.getLogger(__name__)


def _sqlite_connect_args(url: str) -> dict[str, Any]:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _engine_kwargs(url: str) -> dict[str, Any]:
    kw: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = _sqlite_connect_args(url)
    return kw


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(Engine, "connect")
def _sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
    if dbapi_connection.__class__.__module__.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db_schema() -> None:
    """Для локальных тестов / отладки без Alembic (предпочтительно alembic upgrade head)."""
    Base.metadata.create_all(bind=engine)
    logger.info("wip_db: create_all applied (dev only)")
