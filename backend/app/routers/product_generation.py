from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import require_admin_user
from app.models.user import User
from app.schemas.product_generation import (
    ProductGenerationJobCreate,
    ProductGenerationJobListResponse,
    ProductGenerationJobOut,
    ProductGenerationJobUpdate,
)
from app.services import product_generation_service as pg_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/product-generation", tags=["product-generation"])


@router.post("/jobs", response_model=ProductGenerationJobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
    body: ProductGenerationJobCreate | None = Body(default=None),
) -> ProductGenerationJobOut:
    job = pg_service.create_job(db=db, user=current_user, payload=body)
    return ProductGenerationJobOut.model_validate(job)


@router.get("/jobs", response_model=ProductGenerationJobListResponse)
def list_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ProductGenerationJobListResponse:
    rows = pg_service.list_jobs(db=db, user=current_user)
    return ProductGenerationJobListResponse(items=[ProductGenerationJobOut.model_validate(r) for r in rows])


@router.get("/jobs/{job_id}", response_model=ProductGenerationJobOut)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ProductGenerationJobOut:
    job = pg_service.get_job_for_user(db=db, user=current_user, job_id=job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return ProductGenerationJobOut.model_validate(job)


@router.patch("/jobs/{job_id}", response_model=ProductGenerationJobOut)
def patch_job(
    job_id: str,
    body: ProductGenerationJobUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ProductGenerationJobOut:
    try:
        job = pg_service.update_job(db=db, user=current_user, job_id=job_id, payload=body)
    except ValueError as exc:
        if str(exc) == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена") from exc
        if str(exc) == "bad_status":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый статус") from exc
        logger.exception("product_generation: unexpected ValueError")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка") from exc
    return ProductGenerationJobOut.model_validate(job)
