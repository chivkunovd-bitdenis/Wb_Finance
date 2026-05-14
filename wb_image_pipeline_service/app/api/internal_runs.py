from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
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
    stop_run,
)
from app.services.pipeline_content_series_step import prepare_content_generation
from celery_app.pipeline_tasks import enqueue_content_series_chain, enqueue_pg32_stub_chain

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


@router.post("/runs/{run_id}/content", response_model=RunCreateResponse)
def post_run_content(
    _auth: InternalAuth,
    run_id: str,
    body: dict[str, str],
    db: Annotated[Session, Depends(get_db)],
) -> RunCreateResponse:
    selected_asset_id = str(body.get("selected_asset_id") or "").strip()
    try:
        should_enqueue = prepare_content_generation(
            db,
            run_id=run_id,
            selected_asset_id=selected_asset_id,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "run_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
        if code == "selected_asset_required":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="selected_asset_id is required") from exc
        if code == "selected_asset_not_found":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected asset is not a generated main frame") from exc
        if code == "run_not_ready":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run is not ready for content generation") from exc
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Content generation failed") from exc
    if should_enqueue:
        try:
            enqueue_content_series_chain(run_id)
        except Exception as exc:
            logger.exception("wip_internal_runs: content enqueue failed run_id=%s", run_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to enqueue content generation tasks",
            ) from exc
    return RunCreateResponse(id=run_id, status="running" if should_enqueue else "completed")


@router.post("/runs/{run_id}/stop", response_model=RunCreateResponse)
def post_run_stop(
    _auth: InternalAuth,
    run_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> RunCreateResponse:
    run = stop_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return RunCreateResponse(id=run_id, status=str(run.status))


@router.get("/runs/{run_id}/assets/{asset_id}/file")
def get_run_asset_file(
    _auth: InternalAuth,
    run_id: str,
    asset_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> FileResponse:
    run = get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    asset = next((a for a in run.assets if str(a.id) == str(asset_id)), None)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    root = Path(settings.media_root).resolve()
    rel = str(asset.storage_rel_path or "").strip()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset path escapes media root") from exc
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset file not found")

    filename = Path(rel).name or f"{asset.id}.png"
    media_type = asset.mime_type or "application/octet-stream"
    return FileResponse(path=str(path), media_type=media_type, filename=filename)
