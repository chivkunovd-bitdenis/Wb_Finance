"""PG-B.2: шаг `structure_main` — OpenAI → SEO + 4 промпта, метаданные в `PipelineStep.meta_json`."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineRun, PipelineStep
from app.schemas.structure_main import StructureMainResult
from app.services.pipeline_pg32_stub import STRUCTURE_STEP_KEY
from app.services.structure_main_openai import call_structure_main_model

logger = logging.getLogger(__name__)


def _session() -> Session:
    from app.db import SessionLocal

    return SessionLocal()


def _structure_meta_ok(meta: dict[str, Any] | None) -> bool:
    if not meta:
        return False
    try:
        StructureMainResult.model_validate(meta)
    except Exception:
        return False
    return True


def apply_structure_main_step(prev: dict[str, Any]) -> dict[str, Any]:
    """
    Выполняет шаг structure_main (идемпотентно при `done` + валидный meta_json).

    `prev` — выход `apply_run_created`: ``{"run_id", "step_id"}`` где ``step_id`` — stub-шаг.
    """
    run_id = str(prev["run_id"])
    stub_step_id = str(prev["step_id"])

    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        if run is None:
            raise ValueError(f"structure_main: run not found run_id={run_id}")

        stub_step = db.scalars(select(PipelineStep).where(PipelineStep.id == stub_step_id)).one_or_none()
        if stub_step is None or stub_step.run_id != run.id:
            raise ValueError("structure_main: stub step mismatch")

        st_step = db.scalars(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == STRUCTURE_STEP_KEY,
            )
        ).one_or_none()
        if st_step is None:
            raise ValueError("structure_main: structure step missing")

        if st_step.status == "done" and _structure_meta_ok(st_step.meta_json if isinstance(st_step.meta_json, dict) else None):
            logger.info("wip_structure_main: idempotent skip run_id=%s", run_id)
            return {"run_id": run_id, "step_id": stub_step_id}

        if st_step.status == "failed":
            raise RuntimeError(
                f"structure_main: step already failed run_id={run_id} err={st_step.error_message!r}"
            )

        payload = dict(run.payload_json or {})
        user_prompt = payload.get("wip_effective_image_prompt")
        if not isinstance(user_prompt, str) or not user_prompt.strip():
            msg = "missing wip_effective_image_prompt in run payload"
            st_step.status = "failed"
            st_step.error_message = msg
            run.status = "failed"
            db.add(st_step)
            db.add(run)
            db.commit()
            raise ValueError(msg)

        st_step.status = "running"
        st_step.error_message = None
        db.add(st_step)
        db.commit()
        db.refresh(st_step)

        try:
            result = call_structure_main_model(user_prompt=user_prompt)
        except Exception as exc:
            logger.exception("wip_structure_main: OpenAI failed run_id=%s", run_id)
            st_step.status = "failed"
            st_step.error_message = str(exc)[:2000]
            run.status = "failed"
            db.add(st_step)
            db.add(run)
            db.commit()
            raise

        meta = result.model_dump()
        st_step.meta_json = meta
        st_step.status = "done"
        st_step.error_message = None
        db.add(st_step)
        db.commit()

        logger.info("wip_structure_main: done run_id=%s step_id=%s", run_id, st_step.id)
        return {"run_id": run_id, "step_id": stub_step_id}
    finally:
        db.close()
