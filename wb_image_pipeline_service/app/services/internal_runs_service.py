"""PG-3.3: создание run и чтение статуса/метаданных для внутреннего HTTP API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.pipeline import PipelineRun

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
