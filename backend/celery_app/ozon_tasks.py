from __future__ import annotations

from celery_app.celery import celery_app
from app.db import SessionLocal
from app.services.marketplace_finance_service import run_sync_run


@celery_app.task(name="ozon_sync_run")
def ozon_sync_run(run_id: str) -> dict:
    """Execute the stored Ozon sync request; never pass credentials through Celery."""
    db = SessionLocal()
    try:
        return run_sync_run(db=db, run_id=run_id)
    finally:
        db.close()
