"""PG-3.2+PG-B.3: Celery chains for initial and content image generation."""

from __future__ import annotations

import logging
from typing import Any

from celery import chain
from sqlalchemy.exc import SQLAlchemyError

from app.services.pipeline_content_series_step import (
    apply_content_done,
    apply_content_images_step,
    apply_content_structure_step,
)
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


@celery_app.task(
    bind=True,
    name="wb_image_pipeline.content_structure",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def content_structure(self: Any, run_id: str) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.content_structure run_id=%s retries=%s",
        run_id,
        getattr(self.request, "retries", 0),
    )
    return apply_content_structure_step(run_id)


@celery_app.task(
    bind=True,
    name="wb_image_pipeline.content_images",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def content_images(self: Any, prev: dict[str, Any]) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.content_images prev=%s retries=%s",
        prev,
        getattr(self.request, "retries", 0),
    )
    return apply_content_images_step(prev)


@celery_app.task(
    bind=True,
    name="wb_image_pipeline.content_done",
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def content_done(self: Any, prev: dict[str, Any]) -> dict[str, Any]:
    logger.info(
        "wb_image_pipeline.content_done payload=%s retries=%s",
        prev,
        getattr(self.request, "retries", 0),
    )
    return apply_content_done(prev)


def enqueue_content_series_chain(run_id: str) -> Any:
    """Ставит в очередь второй этап: GPT 7 промптов → 7 image edits → финализация."""
    sig = chain(content_structure.s(run_id), content_images.s(), content_done.s())
    return sig.apply_async()
