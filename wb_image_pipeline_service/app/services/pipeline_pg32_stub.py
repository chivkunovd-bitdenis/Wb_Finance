"""
PG-3.2 / PG-B.2: run_created готовит run и два шага (`structure_main`, `pg32_stub`);
затем Celery вызывает OpenAI structure и финализирует stub.

PG-B.0: первый шаг цепочки опирается только на `wip_runs.payload_json` от монолита
(`reference_asset_ids`, `description_user`, …); поля карточки WB не требуются.
Перед commit вшивается `wip_effective_image_prompt` (PG-B.1, `image_run_prompt`).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineRun, PipelineStep
from app.services.image_run_prompt import bake_prompt_fields

logger = logging.getLogger(__name__)

STRUCTURE_STEP_KEY = "structure_main"
STUB_STEP_KEY = "pg32_stub"
STRUCTURE_STEP_ORDINAL = 0
STUB_STEP_ORDINAL = 1


def _maybe_merge_baked_prompt(db: Session, run: PipelineRun) -> None:
    """Идемпотентно добавляет в payload поля PG-B.1 (шаблон + description_user)."""
    payload: dict[str, Any] = dict(run.payload_json or {})
    existing = payload.get("wip_effective_image_prompt")
    if isinstance(existing, str) and existing.strip():
        return
    baked = bake_prompt_fields(payload)
    payload.update(baked)
    run.payload_json = payload
    db.add(run)


def _session() -> Session:
    # Ленивый импорт: в тестах после `importlib.reload(app.db)` старый `SessionLocal` не должен кэшироваться.
    from app.db import SessionLocal

    return SessionLocal()


def apply_run_created(run_id: str) -> dict[str, Any]:
    """
    Переводит run из `created` в `running`, создаёт шаги `structure_main` (0) и `pg32_stub` (1), bake промпта.

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

        structure_step = db.scalars(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == STRUCTURE_STEP_KEY,
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

        if stub_step is not None and structure_step is None:
            # Legacy: только stub с ordinal 0 — вставляем structure_main перед ним.
            if stub_step.ordinal == STRUCTURE_STEP_ORDINAL:
                stub_step.ordinal = STUB_STEP_ORDINAL
            structure_step = PipelineStep(
                run_id=run.id,
                step_key=STRUCTURE_STEP_KEY,
                ordinal=STRUCTURE_STEP_ORDINAL,
                status="pending",
            )
            db.add(structure_step)
            db.flush()
            logger.info("wip_pg32: migrated legacy run_id=%s added structure_main", run.id)

        if stub_step is None and structure_step is None:
            structure_step = PipelineStep(
                run_id=run.id,
                step_key=STRUCTURE_STEP_KEY,
                ordinal=STRUCTURE_STEP_ORDINAL,
                status="pending",
            )
            stub_step = PipelineStep(
                run_id=run.id,
                step_key=STUB_STEP_KEY,
                ordinal=STUB_STEP_ORDINAL,
                status="pending",
            )
            db.add(structure_step)
            db.add(stub_step)
            db.flush()
            logger.info(
                "wip_pg32: created steps run_id=%s structure_id=%s stub_id=%s",
                run.id,
                structure_step.id,
                stub_step.id,
            )
        elif stub_step is None or structure_step is None:
            msg = f"wip_pg32: inconsistent steps run_id={run_id}"
            logger.error(msg)
            raise RuntimeError(msg)

        if run.status == "created":
            run.status = "running"

        _maybe_merge_baked_prompt(db, run)

        db.commit()
        db.refresh(run)
        db.refresh(stub_step)

        logger.info(
            "wip_pg32: run_created done run_id=%s stub_id=%s run_status=%s stub_status=%s",
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
