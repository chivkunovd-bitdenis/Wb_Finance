from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.ai_module import (
    AiHypothesisDailyLogResponse,
    AiHypothesisDailyLogUpsertRequest,
    AiHypothesisDailyLogItem,
    AiHypothesisFinishRequest,
    AiHypothesisFinishResponse,
    AiHypothesisItem,
    AiHypothesisListResponse,
    AiHypothesisStartResponse,
    AiTaskItem,
    AiTaskListResponse,
    AiTaskUpdateRequest,
)
from app.services.ai_module_service import (
    InvalidTransitionError,
    NotFoundError,
    finish_hypothesis,
    get_hypothesis,
    get_task,
    list_hypotheses,
    list_tasks,
    start_hypothesis,
    upsert_hypothesis_daily_log,
    update_task_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-module"])


@router.get("/tasks", response_model=AiTaskListResponse)
def ai_tasks_list(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiTaskListResponse:
    items = list_tasks(db=db, user_id=str(current_user.id))
    return AiTaskListResponse(items=[AiTaskItem.model_validate(x) for x in items])


@router.get("/tasks/{task_id}", response_model=AiTaskItem)
def ai_task_get(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiTaskItem:
    try:
        row = get_task(db=db, user_id=str(current_user.id), task_id=task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return AiTaskItem.model_validate(row)


@router.patch("/tasks/{task_id}", response_model=AiTaskItem)
def ai_task_patch(
    task_id: str,
    body: AiTaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiTaskItem:
    try:
        row = update_task_status(db=db, user_id=str(current_user.id), task_id=task_id, status=body.status)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiTaskItem.model_validate(row)


@router.get("/hypotheses", response_model=AiHypothesisListResponse)
def ai_hypotheses_list(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiHypothesisListResponse:
    items = list_hypotheses(db=db, user_id=str(current_user.id))
    return AiHypothesisListResponse(items=[AiHypothesisItem.model_validate(x) for x in items])


@router.get("/hypotheses/{hypothesis_id}", response_model=AiHypothesisItem)
def ai_hypothesis_get(
    hypothesis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiHypothesisItem:
    try:
        row = get_hypothesis(db=db, user_id=str(current_user.id), hypothesis_id=hypothesis_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return AiHypothesisItem.model_validate(row)


@router.post("/hypotheses/{hypothesis_id}/start", response_model=AiHypothesisStartResponse)
def ai_hypothesis_start(
    hypothesis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiHypothesisStartResponse:
    try:
        start_hypothesis(db=db, user_id=str(current_user.id), hypothesis_id=hypothesis_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiHypothesisStartResponse(status="ok")


@router.post("/hypotheses/{hypothesis_id}/finish", response_model=AiHypothesisFinishResponse)
def ai_hypothesis_finish(
    hypothesis_id: str,
    body: AiHypothesisFinishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiHypothesisFinishResponse:
    try:
        finish_hypothesis(
            db=db,
            user_id=str(current_user.id),
            hypothesis_id=hypothesis_id,
            result_summary=body.result_summary,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiHypothesisFinishResponse(status="ok")


@router.post(
    "/hypotheses/{hypothesis_id}/daily-log",
    response_model=AiHypothesisDailyLogResponse,
)
def ai_hypothesis_daily_log_upsert(
    hypothesis_id: str,
    body: AiHypothesisDailyLogUpsertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiHypothesisDailyLogResponse:
    try:
        items = upsert_hypothesis_daily_log(
            db=db,
            user_id=str(current_user.id),
            hypothesis_id=hypothesis_id,
            day=body.day,
            happened=body.happened,
            changed=body.changed,
            unchanged=body.unchanged,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiHypothesisDailyLogResponse(
        items=[
            AiHypothesisDailyLogItem(
                day=x.day,
                happened=x.happened,
                changed=x.changed,
                unchanged=x.unchanged,
                created_at=x.created_at,
                updated_at=x.updated_at,
            )
            for x in items
        ],
    )

