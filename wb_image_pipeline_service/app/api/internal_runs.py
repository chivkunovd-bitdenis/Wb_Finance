from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import InternalAuth
from app.schemas.internal_runs import (
    MonolithImageRunPayload,
    PipelineAssetOut,
    PipelineStepOut,
    RunCreateBody,
    RunCreateResponse,
    RunDetailResponse,
)
from app.services.internal_runs_service import (
    PipelineEnqueueError,
    create_run,
    get_run_by_id,
)
from celery_app.pipeline_tasks import enqueue_pg32_stub_chain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.post("/runs", response_model=RunCreateResponse, status_code=status.HTTP_201_CREATED)
def post_run(
    _auth: InternalAuth,
    body: RunCreateBody,
    db: Annotated[Session, Depends(get_db)],
) -> RunCreateResponse:
    if body.payload is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="payload is required",
        )
    try:
        normalized = MonolithImageRunPayload.model_validate(body.payload).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=json.loads(exc.json(include_url=False)),
        ) from exc
    try:
        run = create_run(
            db,
            monolith_job_id=body.monolith_job_id,
            payload=normalized,
            enqueue_chain=enqueue_pg32_stub_chain,
        )
    except PipelineEnqueueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to enqueue pipeline tasks",
        ) from None
    logger.info(
        "wip_internal_runs: created run_id=%s monolith_job_id=%s",
        run.id,
        body.monolith_job_id,
    )
    return RunCreateResponse(id=str(run.id), status=str(run.status))


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    _auth: InternalAuth,
    run_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> RunDetailResponse:
    run = get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    steps = sorted(run.steps, key=lambda s: (s.ordinal, s.id))
    return RunDetailResponse(
        id=str(run.id),
        status=str(run.status),
        monolith_job_id=run.monolith_job_id,
        payload=run.payload_json,
        created_at=run.created_at,
        updated_at=run.updated_at,
        steps=[PipelineStepOut.model_validate(s) for s in steps],
        assets=[PipelineAssetOut.model_validate(a) for a in run.assets],
    )
