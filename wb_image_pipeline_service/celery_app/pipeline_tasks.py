"""PG-3.2: Celery chain run_created → step_done (stub) с брокером Redis."""

from __future__ import annotations

import logging
from typing import Any

from celery import chain
from sqlalchemy.exc import SQLAlchemyError

from app.services.pipeline_pg32_stub import apply_run_created, apply_step_done
from celery_app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="wb_image_pipeline.run_created",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def run_created(self: Any, run_id: str) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.run_created run_id=%s retries=%s",
        run_id,
        getattr(self.request, "retries", 0),
    )
    return apply_run_created(run_id)


@celery_app.task(
    bind=True,
    name="wb_image_pipeline.step_done",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def step_done(self: Any, prev: dict[str, Any]) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.step_done payload=%s retries=%s",
        prev,
        getattr(self.request, "retries", 0),
    )
    return apply_step_done(prev)


def enqueue_pg32_stub_chain(run_id: str) -> Any:
    """Ставит в очередь цепочку-заглушку PG-3.2 для существующего `wip_runs.id`."""
    sig = chain(run_created.s(run_id), step_done.s())
    return sig.apply_async()
