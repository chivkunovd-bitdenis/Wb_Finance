"""
PG-3.2: идемпотентная «заглушка» пайплайна — run created → один шаг → completed.

Повторные вызовы (ретраи Celery) не должны портить финальный статус и не дублировать шаг.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineRun, PipelineStep

logger = logging.getLogger(__name__)

STUB_STEP_KEY = "pg32_stub"
STUB_STEP_ORDINAL = 0


def _session() -> Session:
    # Ленивый импорт: в тестах после `importlib.reload(app.db)` старый `SessionLocal` не должен кэшироваться.
    from app.db import SessionLocal

    return SessionLocal()


def apply_run_created(run_id: str) -> dict[str, Any]:
    """
    Переводит run из `created` в `running`, создаёт (или находит) stub-step в `running`.
    Идемпотентно при повторном вызове для того же run.
    """
    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        if run is None:
            msg = f"wip_pg32: run not found run_id={run_id}"
            logger.error(msg)
            raise ValueError(msg)

        stub_step = db.scalars(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == STUB_STEP_KEY,
            )
        ).one_or_none()

        if run.status == "completed" and stub_step is not None and stub_step.status == "done":
            logger.info(
                "wip_pg32: run_created idempotent (already completed) run_id=%s step_id=%s",
                run_id,
                stub_step.id,
            )
            return {"run_id": run.id, "step_id": stub_step.id}

        if run.status == "completed":
            msg = f"wip_pg32: invariant broken run completed without stub step run_id={run_id}"
            logger.error(msg)
            raise RuntimeError(msg)

        if stub_step is None:
            stub_step = PipelineStep(
                run_id=run.id,
                step_key=STUB_STEP_KEY,
                ordinal=STUB_STEP_ORDINAL,
                status="pending",
            )
            db.add(stub_step)
            db.flush()
            logger.info(
                "wip_pg32: created stub step run_id=%s step_id=%s",
                run.id,
                stub_step.id,
            )

        if run.status == "created":
            run.status = "running"
        if stub_step.status == "pending":
            stub_step.status = "running"

        db.commit()
        db.refresh(run)
        db.refresh(stub_step)

        logger.info(
            "wip_pg32: run_created done run_id=%s step_id=%s run_status=%s step_status=%s",
            run.id,
            stub_step.id,
            run.status,
            stub_step.status,
        )
        return {"run_id": run.id, "step_id": stub_step.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def apply_step_done(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Завершает stub-step и run. Идемпотентно при повторном вызове.
    """
    run_id = str(payload["run_id"])
    step_id = str(payload["step_id"])

    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        step = db.scalars(select(PipelineStep).where(PipelineStep.id == step_id)).one_or_none()
        if run is None or step is None:
            msg = f"wip_pg32: step_done missing row run_id={run_id} step_id={step_id}"
            logger.error(msg)
            raise ValueError(msg)
        if step.run_id != run.id:
            msg = f"wip_pg32: step_done run mismatch run_id={run_id} step_id={step_id}"
            logger.error(msg)
            raise ValueError(msg)

        if step.status == "done" and run.status == "completed":
            logger.info(
                "wip_pg32: step_done idempotent run_id=%s step_id=%s",
                run_id,
                step_id,
            )
            return {"run_id": run.id, "step_id": step.id, "status": run.status}

        step.status = "done"
        run.status = "completed"

        db.commit()
        db.refresh(run)
        db.refresh(step)

        logger.info(
            "wip_pg32: step_done done run_id=%s step_id=%s",
            run.id,
            step.id,
        )
        return {"run_id": run.id, "step_id": step.id, "status": run.status}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
