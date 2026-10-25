from __future__ import annotations

import logging
from datetime import date

from celery_app.celery import celery_app
from app.db import SessionLocal
from app.models.ozon import OzonSyncRun
from app.services.marketplace_finance_service import (
    chain_backfill_after_run,
    plan_daily_refresh_for_ready_connections,
    run_sync_run,
)

logger = logging.getLogger(__name__)


def _dispatch(db, run: OzonSyncRun) -> None:
    """Queue a run by id only; a broker failure ends that queued run visibly instead of leaving it hanging."""
    try:
        ozon_sync_run.delay(str(run.id))
    except Exception:
        run.status = "failed"
        run.error_message = "Очередь синхронизации недоступна; попробуйте позже."
        db.add(run)
        db.commit()
        logger.warning("Ozon sync run could not be queued run=%s", run.id)


@celery_app.task(name="ozon_sync_run")
def ozon_sync_run(run_id: str) -> dict:
    """Execute the stored Ozon sync request; never pass credentials through Celery."""
    db = SessionLocal()
    try:
        result = run_sync_run(db=db, run_id=run_id)
        # run_sync_run has released the owner lock, so the next bounded backfill chunk can start now.
        follow_up = chain_backfill_after_run(db=db, run_id=run_id, today=date.today())
        if follow_up is not None:
            _dispatch(db, follow_up)
        return result
    finally:
        db.close()


@celery_app.task(name="ozon_daily_refresh")
def ozon_daily_refresh() -> dict:
    """Beat job: resume unfinished backfills, else refresh the last three completed days for ready connections."""
    db = SessionLocal()
    try:
        run_ids = plan_daily_refresh_for_ready_connections(db=db, today=date.today())
        for run_id in run_ids:
            run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
            if run is not None:
                _dispatch(db, run)
        return {"queued": len(run_ids)}
    finally:
        db.close()
