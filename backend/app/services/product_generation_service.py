from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.product_generation_job import ProductGenerationJob
from app.models.user import User
from app.schemas.product_generation import (
    ProductGenerationJobCreate,
    ProductGenerationJobUpdate,
)
from app.services.product_generation_assets import (
    resolve_reference_path,
    save_reference_uploads,
)
from app.services.product_generation_image_pipeline import (
    ImagePipelineClientError,
    build_image_pipeline_payload,
    create_remote_run,
    is_image_pipeline_enabled,
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


async def append_job_references(
    *,
    db: Session,
    user: User,
    job_id: str,
    uploads: list[UploadFile],
) -> ProductGenerationJob:
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        raise ValueError("not_found")
    if job.status != "draft":
        raise ValueError("bad_status_upload")
    records, _written = await save_reference_uploads(
        user_id=str(user.id),
        job_id=str(job.id),
        uploads=uploads,
    )
    prev: list[Any] = list(job.reference_paths_json or [])
    prev.extend(records)
    job.reference_paths_json = prev
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("product_generation: appended %s reference(s) to job %s", len(records), job.id)
    return job


def get_reference_file(
    *,
    db: Session,
    user: User,
    job_id: str,
    asset_id: str,
) -> tuple[Path, dict[str, Any]]:
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        raise ValueError("not_found")
    refs = list(job.reference_paths_json or [])
    match: dict[str, Any] | None = None
    for r in refs:
        if isinstance(r, dict) and str(r.get("asset_id") or "") == asset_id:
            match = r
            break
    if not match:
        raise ValueError("asset_not_found")
    stored = str(match.get("stored_name") or "")
    path = resolve_reference_path(user_id=str(user.id), job_id=str(job.id), stored_name=stored)
    if path is None or not path.is_file():
        raise ValueError("missing_file")
    return path, match


def start_job_pipeline(*, db: Session, user: User, job_id: str) -> ProductGenerationJob:
    """
    Переводит черновик в in_progress и назначает pipeline_run_id.

    Условия старта (PG-A.1): статус `draft`, есть ≥1 референс в `reference_paths_json`.
    Не требуются `title`, `vendor_code`, размеры, цена, габариты — они для публикации/WB позже.

    Если заданы `PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL` и `PRODUCT_GEN_IMAGE_PIPELINE_SECRET`,
    создаётся run в wb_image_pipeline_service (POST /internal/v1/runs). Иначе — локальный
    placeholder `local-*` и Celery-заглушка монолита (PG-2.3).
    """
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        raise ValueError("not_found")
    if job.status != "draft":
        raise ValueError("bad_status_start")
    refs = list(job.reference_paths_json or [])
    if len(refs) == 0:
        raise ValueError("no_references")

    if is_image_pipeline_enabled():
        payload = build_image_pipeline_payload(job)
        try:
            run_id = create_remote_run(str(job.id), payload)
        except ImagePipelineClientError as exc:
            logger.warning("product_generation: remote pipeline create failed job=%s: %s", job_id, exc)
            raise ValueError("image_pipeline_unavailable") from exc
        job.status = "in_progress"
        job.pipeline_run_id = run_id
        db.add(job)
        db.commit()
        db.refresh(job)
        logger.info(
            "product_generation: remote pipeline start job=%s run=%s",
            job.id,
            job.pipeline_run_id,
        )
        return job

    job.status = "in_progress"
    job.pipeline_run_id = f"local-{uuid.uuid4()}"
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("product_generation: pipeline start job=%s run=%s", job.id, job.pipeline_run_id)
    return job


def should_enqueue_monolith_celery_stub(job: ProductGenerationJob) -> bool:
    """True, если нужна Celery-заглушка монолита (локальный run)."""
    rid = str(job.pipeline_run_id or "")
    return rid.startswith("local-")


def revert_local_pipeline_start(*, db: Session, user: User, job_id: str) -> None:
    """Откат, если Celery не принял задачу (только placeholder-run local-*)."""
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        return
    rid = str(job.pipeline_run_id or "")
    if job.status == "in_progress" and rid.startswith("local-"):
        job.status = "draft"
        job.pipeline_run_id = None
        db.add(job)
        db.commit()
        logger.warning("product_generation: reverted pipeline start (enqueue failed) job=%s", job_id)


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
