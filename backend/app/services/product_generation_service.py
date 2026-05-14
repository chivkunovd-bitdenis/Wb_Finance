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
    start_remote_content_generation,
    stop_remote_run,
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


def get_reference_file_by_job_id(
    *,
    db: Session,
    job_id: str,
    asset_id: str,
) -> tuple[Path, dict[str, Any]]:
    """Internal service access for WIP: resolve a reference without admin user context."""
    job = db.query(ProductGenerationJob).filter(ProductGenerationJob.id == job_id).first()
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
    path = resolve_reference_path(user_id=str(job.user_id), job_id=str(job.id), stored_name=stored)
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
    if job.status not in {"draft", "error"}:
        raise ValueError("bad_status_start")
    refs = list(job.reference_paths_json or [])
    if len(refs) == 0:
        raise ValueError("no_references")

    if is_image_pipeline_enabled():
        old_rid = str(job.pipeline_run_id or "").strip()
        if job.status == "error" and old_rid and not old_rid.startswith("local-"):
            try:
                stop_remote_run(old_rid)
            except ImagePipelineClientError as exc:
                logger.warning("product_generation: old remote pipeline stop failed job=%s run=%s: %s", job_id, old_rid, exc)
                raise ValueError("image_pipeline_unavailable") from exc
        payload = build_image_pipeline_payload(job)
        try:
            run_id = create_remote_run(str(job.id), payload)
        except ImagePipelineClientError as exc:
            logger.warning("product_generation: remote pipeline create failed job=%s: %s", job_id, exc)
            raise ValueError("image_pipeline_unavailable") from exc
        job.status = "in_progress"
        job.pipeline_run_id = run_id
        job.wb_publish_error = None
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
    job.wb_publish_error = None
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


def stop_job_pipeline(*, db: Session, user: User, job_id: str) -> ProductGenerationJob:
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        raise ValueError("not_found")
    if job.status != "in_progress":
        raise ValueError("bad_status_stop")

    rid = str(job.pipeline_run_id or "").strip()
    if rid and not rid.startswith("local-") and is_image_pipeline_enabled():
        try:
            stop_remote_run(rid)
        except ImagePipelineClientError as exc:
            logger.warning("product_generation: remote pipeline stop failed job=%s run=%s: %s", job_id, rid, exc)
            raise ValueError("image_pipeline_unavailable") from exc

    job.status = "error"
    job.wb_publish_error = "stopped manually before retry"
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


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


def start_job_content_generation(
    *,
    db: Session,
    user: User,
    job_id: str,
    selected_asset_id: str,
) -> ProductGenerationJob:
    job = get_job_for_user(db=db, user=user, job_id=job_id)
    if not job:
        raise ValueError("not_found")
    rid = str(job.pipeline_run_id or "").strip()
    if not rid or rid.startswith("local-"):
        raise ValueError("remote_run_required")
    selected = str(selected_asset_id or "").strip()
    if not selected:
        raise ValueError("selected_asset_required")
    if not is_image_pipeline_enabled():
        raise ValueError("image_pipeline_unavailable")
    try:
        start_remote_content_generation(rid, selected)
    except ImagePipelineClientError as exc:
        raise ValueError("image_pipeline_unavailable") from exc
    job.selected_main_asset_id = selected
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("product_generation: content generation started job=%s run=%s selected=%s", job.id, rid, selected)
    return job
