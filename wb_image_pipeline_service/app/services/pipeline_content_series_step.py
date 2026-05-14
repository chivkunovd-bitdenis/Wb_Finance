"""PG-C.2: второй этап — 7 контентных фото WB по выбранному главному кадру."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineAsset, PipelineRun, PipelineStep
from app.schemas.content_series import ContentSeriesResult
from app.services.content_series_openai import call_content_series_model
from app.services.image_generation_prompts import build_content_image_prompt
from app.services.images_main_openai import call_openai_image_bytes
from app.services.reference_fetch_client import ReferenceImage

logger = logging.getLogger(__name__)

CONTENT_STRUCTURE_STEP_KEY = "content_structure"
CONTENT_IMAGES_STEP_KEY = "content_images"
CONTENT_STRUCTURE_STEP_ORDINAL = 3
CONTENT_IMAGES_STEP_ORDINAL = 4
CONTENT_FRAME_KIND = "content_frame"
CONTENT_FRAME_COUNT = 7


def _session() -> Session:
    from app.db import SessionLocal

    return SessionLocal()


def _media_root() -> Path:
    from app.config import settings

    return Path(settings.media_root).resolve()


def _file_under_media(fp: Path, root: Path) -> bool:
    try:
        fp.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _asset_path(asset: PipelineAsset) -> Path:
    return (_media_root() / str(asset.storage_rel_path or "")).resolve()


def _main_asset_reference(asset: PipelineAsset) -> ReferenceImage:
    path = _asset_path(asset)
    root = _media_root()
    if not _file_under_media(path, root) or not path.is_file():
        raise ValueError("selected asset file is missing")
    raw = path.read_bytes()
    if not raw:
        raise ValueError("selected asset file is empty")
    digest = hashlib.sha256(raw).hexdigest()
    return ReferenceImage(
        asset_id=str(asset.id),
        filename=path.name or f"{asset.id}.png",
        mime_type=asset.mime_type or "image/png",
        content=raw,
        sha256_hex=digest,
    )


def _get_step(db: Session, *, run_id: str, step_key: str) -> PipelineStep | None:
    return db.scalars(
        select(PipelineStep).where(
            PipelineStep.run_id == run_id,
            PipelineStep.step_key == step_key,
        )
    ).one_or_none()


def _get_selected_main_asset(db: Session, *, run_id: str, selected_asset_id: str) -> PipelineAsset | None:
    return db.scalars(
        select(PipelineAsset).where(
            PipelineAsset.run_id == run_id,
            PipelineAsset.id == selected_asset_id,
            PipelineAsset.kind == "main_frame",
        )
    ).one_or_none()


def _content_asset_count(db: Session, *, run_id: str, selected_asset_id: str) -> int:
    rows = db.scalars(
        select(PipelineAsset).where(
            PipelineAsset.run_id == run_id,
            PipelineAsset.kind == CONTENT_FRAME_KIND,
        )
    ).all()
    total = 0
    for row in rows:
        meta = row.meta_json if isinstance(row.meta_json, dict) else {}
        if str(meta.get("selected_main_asset_id") or "") == selected_asset_id:
            total += 1
    return total


def _wipe_content_assets(db: Session, *, run_id: str) -> None:
    root = _media_root()
    rows = list(
        db.scalars(
            select(PipelineAsset).where(
                PipelineAsset.run_id == run_id,
                PipelineAsset.kind == CONTENT_FRAME_KIND,
            )
        ).all()
    )
    for asset in rows:
        rel = str(asset.storage_rel_path or "").strip()
        if rel:
            fp = (root / rel).resolve()
            try:
                if fp.is_file() and _file_under_media(fp, root):
                    fp.unlink(missing_ok=True)
            except OSError:
                logger.warning("wip_content_series: unlink failed path=%s", fp)
        db.delete(asset)
    db.flush()


def prepare_content_generation(
    db: Session,
    *,
    run_id: str,
    selected_asset_id: str,
) -> bool:
    """
    Готовит run к генерации content-серии.

    Returns:
        True, если нужно поставить Celery chain; False, если серия уже готова или выполняется.
    """
    selected = selected_asset_id.strip()
    run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
    if run is None:
        raise ValueError("run_not_found")
    if not selected:
        raise ValueError("selected_asset_required")
    main_asset = _get_selected_main_asset(db, run_id=run_id, selected_asset_id=selected)
    if main_asset is None:
        raise ValueError("selected_asset_not_found")
    if run.status != "completed":
        current_payload = run.payload_json if isinstance(run.payload_json, dict) else {}
        current_selected = str(current_payload.get("selected_main_asset_id") or "")
        structure_step = _get_step(db, run_id=run_id, step_key=CONTENT_STRUCTURE_STEP_KEY)
        images_step = _get_step(db, run_id=run_id, step_key=CONTENT_IMAGES_STEP_KEY)
        if current_selected == selected and images_step is not None and images_step.status in {"pending", "running"}:
            return False
        failed_content = current_selected == selected and (
            (structure_step is not None and structure_step.status == "failed")
            or (images_step is not None and images_step.status == "failed")
        )
        if not failed_content:
            raise ValueError("run_not_ready")

    payload = dict(run.payload_json or {})
    previous_selected = str(payload.get("selected_main_asset_id") or "")

    structure_step = _get_step(db, run_id=run_id, step_key=CONTENT_STRUCTURE_STEP_KEY)
    if structure_step is None:
        structure_step = PipelineStep(
            run_id=run.id,
            step_key=CONTENT_STRUCTURE_STEP_KEY,
            ordinal=CONTENT_STRUCTURE_STEP_ORDINAL,
            status="pending",
        )
        db.add(structure_step)
        db.flush()

    images_step = _get_step(db, run_id=run_id, step_key=CONTENT_IMAGES_STEP_KEY)
    if images_step is None:
        images_step = PipelineStep(
            run_id=run.id,
            step_key=CONTENT_IMAGES_STEP_KEY,
            ordinal=CONTENT_IMAGES_STEP_ORDINAL,
            status="pending",
        )
        db.add(images_step)
        db.flush()

    already_done = (
        previous_selected == selected
        and structure_step.status == "done"
        and images_step.status == "done"
        and _content_asset_count(db, run_id=run_id, selected_asset_id=selected) >= CONTENT_FRAME_COUNT
    )
    if already_done:
        return False

    if previous_selected != selected:
        _wipe_content_assets(db, run_id=run_id)
    elif _content_asset_count(db, run_id=run_id, selected_asset_id=selected) < CONTENT_FRAME_COUNT:
        _wipe_content_assets(db, run_id=run_id)

    payload["selected_main_asset_id"] = selected
    run.payload_json = payload
    run.status = "running"
    structure_step.status = "pending"
    structure_step.error_message = None
    structure_step.meta_json = None
    images_step.status = "pending"
    images_step.error_message = None
    images_step.meta_json = None
    db.add(run)
    db.add(structure_step)
    db.add(images_step)
    db.commit()
    return True


def _content_result_from_step(step: PipelineStep, *, selected_asset_id: str) -> ContentSeriesResult | None:
    meta = step.meta_json if isinstance(step.meta_json, dict) else None
    if not meta or str(meta.get("selected_main_asset_id") or "") != selected_asset_id:
        return None
    try:
        return ContentSeriesResult.model_validate(meta)
    except Exception:
        return None


def apply_content_structure_step(run_id: str) -> dict[str, Any]:
    """Генерирует 7 JSON-промптов для content-серии."""
    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        if run is None:
            raise ValueError(f"content_structure: run not found run_id={run_id}")
        payload = dict(run.payload_json or {})
        selected_asset_id = str(payload.get("selected_main_asset_id") or "").strip()
        if not selected_asset_id:
            raise ValueError("content_structure: selected_main_asset_id missing")
        main_asset = _get_selected_main_asset(db, run_id=run_id, selected_asset_id=selected_asset_id)
        if main_asset is None:
            raise ValueError("content_structure: selected main asset not found")

        step = _get_step(db, run_id=run_id, step_key=CONTENT_STRUCTURE_STEP_KEY)
        if step is None:
            raise ValueError("content_structure: step missing")
        existing = _content_result_from_step(step, selected_asset_id=selected_asset_id)
        if step.status == "done" and existing is not None:
            return {"run_id": run_id, "selected_asset_id": selected_asset_id}
        if step.status == "failed":
            raise RuntimeError(f"content_structure: step already failed err={step.error_message!r}")

        step.status = "running"
        step.error_message = None
        db.add(step)
        db.commit()
        db.refresh(step)

        asset_meta = main_asset.meta_json if isinstance(main_asset.meta_json, dict) else {}
        selected_prompt = str(asset_meta.get("prompt") or "").strip()
        selected_ref = _main_asset_reference(main_asset)
        try:
            result = call_content_series_model(
                selected_prompt=selected_prompt,
                selected_reference_image=selected_ref,
            )
        except Exception as exc:
            logger.exception("wip_content_structure: OpenAI failed run_id=%s", run_id)
            step.status = "failed"
            step.error_message = str(exc)[:2000]
            run.status = "failed"
            db.add(step)
            db.add(run)
            db.commit()
            raise

        meta = result.model_dump()
        meta["selected_main_asset_id"] = selected_asset_id
        step.meta_json = meta
        step.status = "done"
        step.error_message = None
        db.add(step)
        db.commit()
        return {"run_id": run_id, "selected_asset_id": selected_asset_id}
    finally:
        db.close()


def apply_content_images_step(prev: dict[str, Any]) -> dict[str, Any]:
    """Генерирует 7 content_frame ассетов по выбранному главному кадру."""
    run_id = str(prev["run_id"])
    selected_asset_id = str(prev["selected_asset_id"])
    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        if run is None:
            raise ValueError(f"content_images: run not found run_id={run_id}")
        main_asset = _get_selected_main_asset(db, run_id=run_id, selected_asset_id=selected_asset_id)
        if main_asset is None:
            raise ValueError("content_images: selected main asset not found")

        structure_step = _get_step(db, run_id=run_id, step_key=CONTENT_STRUCTURE_STEP_KEY)
        if structure_step is None or structure_step.status != "done":
            raise ValueError("content_images: content_structure not done")
        structure = _content_result_from_step(structure_step, selected_asset_id=selected_asset_id)
        if structure is None:
            raise ValueError("content_images: invalid content_structure meta_json")

        images_step = _get_step(db, run_id=run_id, step_key=CONTENT_IMAGES_STEP_KEY)
        if images_step is None:
            raise ValueError("content_images: step missing")
        if (
            images_step.status == "done"
            and _content_asset_count(db, run_id=run_id, selected_asset_id=selected_asset_id) >= CONTENT_FRAME_COUNT
        ):
            return {"run_id": run_id, "selected_asset_id": selected_asset_id}
        if images_step.status == "failed":
            raise RuntimeError(f"content_images: step already failed err={images_step.error_message!r}")

        _wipe_content_assets(db, run_id=run_id)
        images_step.status = "running"
        images_step.error_message = None
        db.add(images_step)
        db.commit()
        db.refresh(images_step)

        media_root = _media_root()
        run_dir = media_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        selected_ref = _main_asset_reference(main_asset)
        model_name = (os.getenv("WIP_OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()

        try:
            for idx, prompt in enumerate(structure.series_prompts):
                image_prompt = build_content_image_prompt(prompt)
                raw, mime = call_openai_image_bytes(prompt=image_prompt, reference_images=[selected_ref])
                digest = hashlib.sha256(raw).hexdigest()
                rel_path = f"{run_id}/content_frame_{idx}.png"
                out_path = media_root / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(raw)
                db.add(
                    PipelineAsset(
                        run_id=run.id,
                        step_id=images_step.id,
                        kind=CONTENT_FRAME_KIND,
                        storage_rel_path=rel_path,
                        mime_type=mime,
                        sha256_hex=digest,
                        meta_json={
                            "series_index": idx,
                            "openai_image_model": model_name,
                            "prompt": prompt,
                            "image_prompt": image_prompt,
                            "selected_main_asset_id": selected_asset_id,
                            "reference_asset_ids": [selected_asset_id],
                            "reference_fingerprint": selected_ref.sha256_hex,
                        },
                    )
                )
                db.flush()
        except Exception as exc:
            logger.exception("wip_content_images: generation failed run_id=%s", run_id)
            images_step.status = "failed"
            images_step.error_message = str(exc)[:2000]
            run.status = "failed"
            db.add(images_step)
            db.add(run)
            db.commit()
            raise

        if _content_asset_count(db, run_id=run_id, selected_asset_id=selected_asset_id) < CONTENT_FRAME_COUNT:
            msg = "content_images: fewer than 7 assets after generation"
            images_step.status = "failed"
            images_step.error_message = msg
            run.status = "failed"
            db.add(images_step)
            db.add(run)
            db.commit()
            raise RuntimeError(msg)

        images_step.status = "done"
        images_step.error_message = None
        db.add(images_step)
        db.commit()
        return {"run_id": run_id, "selected_asset_id": selected_asset_id}
    finally:
        db.close()


def apply_content_done(payload: dict[str, Any]) -> dict[str, Any]:
    """Финализирует run после content-серии."""
    run_id = str(payload["run_id"])
    selected_asset_id = str(payload["selected_asset_id"])
    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        if run is None:
            raise ValueError(f"content_done: run not found run_id={run_id}")
        images_step = _get_step(db, run_id=run_id, step_key=CONTENT_IMAGES_STEP_KEY)
        if images_step is None or images_step.status != "done":
            raise ValueError("content_done: content_images not done")
        run.status = "completed"
        db.add(run)
        db.commit()
        return {"run_id": run_id, "selected_asset_id": selected_asset_id, "status": run.status}
    finally:
        db.close()
