from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.product_generation_job import ProductGenerationJob
from app.models.user import User
from app.schemas.product_generation import (
    ProductGenerationJobCreate,
    ProductGenerationJobUpdate,
)

logger = logging.getLogger(__name__)

_ALLOWED_STATUS = frozenset({"draft", "in_progress", "error", "ready_to_publish", "published"})


def _sizes_to_json(sizes: list[Any] | None) -> list[dict[str, str]] | None:
    if sizes is None:
        return None
    rows: list[dict[str, str]] = []
    for s in sizes:
        if isinstance(s, dict):
            rows.append({"tech_size": str(s["tech_size"]), "wb_size": str(s["wb_size"])})
        else:
            rows.append({"tech_size": s.tech_size, "wb_size": s.wb_size})
    return rows


def create_job(*, db: Session, user: User, payload: ProductGenerationJobCreate | None) -> ProductGenerationJob:
    data = payload.model_dump(exclude_unset=True) if payload else {}
    sizes = data.pop("sizes", None)
    sizes_json = _sizes_to_json(sizes)
    job = ProductGenerationJob(
        user_id=user.id,
        status="draft",
        sizes_json=sizes_json,
        **{k: v for k, v in data.items() if k != "sizes"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("product_generation: created job %s for user %s", job.id, user.id)
    return job


def list_jobs(*, db: Session, user: User) -> list[ProductGenerationJob]:
    return (
        db.query(ProductGenerationJob)
        .filter(ProductGenerationJob.user_id == user.id)
        .order_by(ProductGenerationJob.created_at.desc())
        .all()
    )


def get_job_for_user(*, db: Session, user: User, job_id: str) -> ProductGenerationJob | None:
    return (
        db.query(ProductGenerationJob)
        .filter(ProductGenerationJob.id == job_id)
        .filter(ProductGenerationJob.user_id == user.id)
        .first()
    )


def update_job(*, db: Session, user: User, job_id: str, payload: ProductGenerationJobUpdate) -> ProductGenerationJob:
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        raise ValueError("not_found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data:
        st = data["status"]
        if st is not None and st not in _ALLOWED_STATUS:
            raise ValueError("bad_status")
    if "sizes" in data:
        sizes = data.pop("sizes")
        job.sizes_json = _sizes_to_json(sizes)
    if "selected_series_asset_ids" in data:
        job.selected_series_asset_ids = data.pop("selected_series_asset_ids")
    for key, val in data.items():
        setattr(job, key, val)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
