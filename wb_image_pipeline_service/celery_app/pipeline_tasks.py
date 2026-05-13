"""PG-3.2+PG-B.3: Celery chain run_created → structure_main → images_main → step_done (stub finalize)."""

from __future__ import annotations

import logging
from typing import Any

from celery import chain
from sqlalchemy.exc import SQLAlchemyError

from app.services.pipeline_images_step import apply_images_main_step
from app.services.pipeline_pg32_stub import apply_run_created, apply_step_done
from app.services.pipeline_structure_step import apply_structure_main_step
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
    name="wb_image_pipeline.structure_main",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def structure_main(self: Any, prev: dict[str, Any]) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.structure_main prev=%s retries=%s",
        prev,
        getattr(self.request, "retries", 0),
    )
    return apply_structure_main_step(prev)


@celery_app.task(
    bind=True,
    name="wb_image_pipeline.images_main",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def images_main(self: Any, prev: dict[str, Any]) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.images_main prev=%s retries=%s",
        prev,
        getattr(self.request, "retries", 0),
    )
    return apply_images_main_step(prev)


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
    """Ставит в очередь цепочку PG-3.2 + PG-B.3: run_created → structure_main → images_main → stub finalize."""
    sig = chain(run_created.s(run_id), structure_main.s(), images_main.s(), step_done.s())
    return sig.apply_async()
