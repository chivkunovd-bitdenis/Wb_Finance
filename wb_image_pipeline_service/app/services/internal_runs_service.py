"""PG-3.3: создание run и чтение статуса/метаданных для внутреннего HTTP API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.pipeline import PipelineAsset, PipelineRun, PipelineStep

logger = logging.getLogger(__name__)


class PipelineEnqueueError(Exception):
    """Не удалось поставить Celery-цепочку после сохранения run в БД."""


def create_run(
    db: Session,
    *,
    monolith_job_id: str,
    payload: dict[str, Any] | None,
    enqueue_chain: Callable[[str], Any],
) -> PipelineRun:
    """
    Создаёт строку `wip_runs` и ставит в очередь PG-3.2 chain (идемпотентный stub).

    При ошибке постановки в Celery run уже закоммичен в `created` — клиент может
    повторить интеграционный вызов или удалить run отдельной политикой (вне PG-3.3).
    """
    run = PipelineRun(
        status="created",
        monolith_job_id=monolith_job_id,
        payload_json=payload if payload is not None else {},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = str(run.id)
    try:
        enqueue_chain(run_id)
    except Exception as exc:
        logger.exception("wip_internal_runs: enqueue failed run_id=%s", run_id)
        raise PipelineEnqueueError(run_id) from exc
    return run


def get_run_by_id(db: Session, run_id: str) -> PipelineRun | None:
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.id == run_id)
        .options(
            selectinload(PipelineRun.steps),
            selectinload(PipelineRun.assets),
        )
    )
    return db.scalars(stmt).one_or_none()


def stop_run(db: Session, run_id: str) -> PipelineRun | None:
    """
    Stop a run so queued Celery tasks cannot continue token-spending steps.

    If main frames already exist, keep the run retryable for content generation by marking
    only pending/running content steps failed and leaving the run in `failed`.
    If no main frames exist, cancel the whole run.
    """
    run = get_run_by_id(db, run_id)
    if run is None:
        return None

    has_main_assets = (
        db.query(PipelineAsset.id)
        .filter(PipelineAsset.run_id == run_id, PipelineAsset.kind == "main_frame")
        .first()
        is not None
    )

    if has_main_assets:
        for step in run.steps:
            if step.step_key in {"content_structure", "content_images"} and step.status in {"pending", "running"}:
                step.status = "failed"
                step.error_message = step.error_message or "stopped manually before retry"
                db.add(step)
        run.status = "failed"
    else:
        for step in run.steps:
            if step.status in {"pending", "running"}:
                step.status = "failed"
                step.error_message = step.error_message or "cancelled manually before retry"
                db.add(step)
        run.status = "cancelled"

    db.add(run)
    db.commit()
    db.refresh(run)
    return run
