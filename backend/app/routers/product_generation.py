from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
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
from app.services.product_generation_image_pipeline import (
    ImagePipelineClientError,
    enrich_job_out_with_image_pipeline,
    fetch_remote_asset_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/product-generation", tags=["product-generation"])


def _internal_reference_secret() -> str:
    return (
        os.getenv("PRODUCT_GEN_REFERENCE_FETCH_SECRET")
        or os.getenv("PRODUCT_GEN_IMAGE_PIPELINE_SECRET")
        or ""
    ).strip()


def _require_reference_internal_auth(authorization: str | None = Header(default=None)) -> None:
    secret = _internal_reference_secret()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Internal reference auth is not configured")
    prefix = "Bearer "
    token = authorization[len(prefix) :].strip() if authorization and authorization.startswith(prefix) else ""
    if not token or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


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
    items = [enrich_job_out_with_image_pipeline(ProductGenerationJobOut.model_validate(r)) for r in rows]
    return ProductGenerationJobListResponse(items=items)


@router.get("/jobs/{job_id}", response_model=ProductGenerationJobOut)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ProductGenerationJobOut:
    job = pg_service.get_job_for_user(db=db, user=current_user, job_id=job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return enrich_job_out_with_image_pipeline(ProductGenerationJobOut.model_validate(job))


@router.post("/jobs/{job_id}/references", response_model=ProductGenerationJobOut)
async def upload_job_references(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
    files: list[UploadFile] = File(...),
) -> ProductGenerationJobOut:
    try:
        job = await pg_service.append_job_references(
            db=db,
            user=current_user,
            job_id=job_id,
            uploads=files,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена") from exc
        if code == "bad_status_upload":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Загрузка референсов только для черновика (draft)",
            ) from exc
        if code == "no_files":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет файлов") from exc
        if code == "too_many_files":
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Слишком много файлов") from exc
        if code == "file_too_large":
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Файл слишком большой") from exc
        if code == "bad_content_type":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Допустимы только изображения: JPEG, PNG, WebP, GIF",
            ) from exc
        logger.exception("product_generation: unexpected ValueError on upload")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка") from exc
    return ProductGenerationJobOut.model_validate(job)


@router.get("/jobs/{job_id}/references/{asset_id}/file")
def download_job_reference_file(
    job_id: str,
    asset_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> FileResponse:
    try:
        path, meta = pg_service.get_reference_file(db=db, user=current_user, job_id=job_id, asset_id=asset_id)
    except ValueError as exc:
        code = str(exc)
        if code in {"not_found", "asset_not_found", "missing_file"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден") from exc
        logger.exception("product_generation: unexpected ValueError on download")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка") from exc
    media = str(meta.get("content_type") or "application/octet-stream")
    name = str(meta.get("original_filename") or "reference")
    return FileResponse(path=str(path), media_type=media, filename=name)


@router.get("/internal/jobs/{job_id}/references/{asset_id}/file")
def download_reference_file_internal(
    job_id: str,
    asset_id: str,
    _auth: None = Depends(_require_reference_internal_auth),
    db: Session = Depends(get_db),
) -> FileResponse:
    try:
        path, meta = pg_service.get_reference_file_by_job_id(db=db, job_id=job_id, asset_id=asset_id)
    except ValueError as exc:
        code = str(exc)
        if code in {"not_found", "asset_not_found", "missing_file"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден") from exc
        logger.exception("product_generation: unexpected ValueError on internal reference download")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка") from exc
    media = str(meta.get("content_type") or "application/octet-stream")
    name = str(meta.get("original_filename") or "reference")
    return FileResponse(path=str(path), media_type=media, filename=name)


@router.get("/jobs/{job_id}/generated-assets/{asset_id}/file")
def download_generated_asset_file(
    job_id: str,
    asset_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> Response:
    job = pg_service.get_job_for_user(db=db, user=current_user, job_id=job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    run_id = str(job.pipeline_run_id or "").strip()
    if not run_id or run_id.startswith("local-"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Удалённый image-run не найден")
    try:
        content, media_type, filename = fetch_remote_asset_file(run_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сгенерированное фото не найдено") from exc
    except ImagePipelineClientError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Image-сервис недоступен") from exc
    safe_name = filename.replace('"', "")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/jobs/{job_id}/start", response_model=ProductGenerationJobOut)
def start_job_pipeline(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ProductGenerationJobOut:
    try:
        job = pg_service.start_job_pipeline(db=db, user=current_user, job_id=job_id)
    except ValueError as exc:
        code = str(exc)
        if code == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена") from exc
        if code == "bad_status_start":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Старт пайплайна только из черновика (draft)",
            ) from exc
        if code == "no_references":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Сначала загрузите хотя бы один референс",
            ) from exc
        if code == "image_pipeline_unavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось создать run в image-сервисе",
            ) from exc
        logger.exception("product_generation: unexpected ValueError on start")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка") from exc
    if pg_service.should_enqueue_monolith_celery_stub(job):
        try:
            from celery_app.tasks import product_generation_pipeline_stub as pipeline_stub

            pipeline_stub.delay(str(job.id))
        except Exception as exc:
            logger.exception("product_generation: Celery enqueue failed for job %s", job_id)
            pg_service.revert_local_pipeline_start(db=db, user=current_user, job_id=job_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось поставить задачу в очередь (celery/redis недоступны)",
            ) from exc
    return enrich_job_out_with_image_pipeline(ProductGenerationJobOut.model_validate(job))


@router.post("/jobs/{job_id}/generate-content", response_model=ProductGenerationJobOut)
def start_job_content_generation(
    job_id: str,
    body: dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
) -> ProductGenerationJobOut:
    selected_asset_id = str(body.get("selected_asset_id") or "").strip()
    try:
        job = pg_service.start_job_content_generation(
            db=db,
            user=current_user,
            job_id=job_id,
            selected_asset_id=selected_asset_id,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена") from exc
        if code == "selected_asset_required":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Выберите фото") from exc
        if code == "remote_run_required":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Контент можно генерировать только для удалённого image-run",
            ) from exc
        if code == "image_pipeline_unavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось запустить генерацию контента в image-сервисе",
            ) from exc
        logger.exception("product_generation: unexpected ValueError on generate-content")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Внутренняя ошибка") from exc
    return enrich_job_out_with_image_pipeline(ProductGenerationJobOut.model_validate(job))


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
