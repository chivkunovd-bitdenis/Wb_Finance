"""PG-B.3: шаг `images_main` — 4× OpenAI image → volume + `wip_assets`."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineAsset, PipelineRun, PipelineStep
from app.schemas.structure_main import StructureMainResult
from app.services.image_generation_prompts import build_main_image_prompt
from app.services.images_main_openai import call_openai_image_bytes
from app.services.pipeline_pg32_stub import IMAGES_MAIN_STEP_KEY, STRUCTURE_STEP_KEY
from app.services.reference_fetch_client import fetch_reference_images, reference_metadata

logger = logging.getLogger(__name__)


def _session() -> Session:
    from app.db import SessionLocal

    return SessionLocal()


def _media_root() -> Path:
    from app.config import settings

    return Path(settings.media_root).resolve()


def _structure_result_from_step(st: PipelineStep) -> StructureMainResult | None:
    meta = st.meta_json if isinstance(st.meta_json, dict) else None
    if not meta:
        return None
    try:
        return StructureMainResult.model_validate(meta)
    except Exception:
        return None


def _count_main_frame_assets(db: Session, *, step_id: str) -> int:
    q = select(PipelineAsset.id).where(
        PipelineAsset.step_id == step_id,
        PipelineAsset.kind == "main_frame",
    )
    return len(list(db.scalars(q).all()))


def _file_under_media(fp: Path, root: Path) -> bool:
    try:
        fp.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _wipe_step_assets(db: Session, *, step_id: str, run_id: str) -> None:
    root = _media_root()
    rows = list(db.scalars(select(PipelineAsset).where(PipelineAsset.step_id == step_id)).all())
    for a in rows:
        rel = (a.storage_rel_path or "").strip()
        if rel:
            fp = (root / rel).resolve()
            try:
                if fp.is_file() and _file_under_media(fp, root):
                    fp.unlink(missing_ok=True)
            except OSError:
                logger.warning("wip_images_main: unlink failed path=%s", fp)
        db.delete(a)
    db.flush()
    # prune empty run dir
    try:
        run_dir = root / run_id
        if run_dir.is_dir() and not any(run_dir.iterdir()):
            run_dir.rmdir()
    except OSError:
        pass


def apply_images_main_step(prev: dict[str, Any]) -> dict[str, Any]:
    """
    Генерирует 4 файла по `main_prompts` из шага `structure_main`.

    Идемпотентно при ``done`` + 4 ассета ``main_frame`` на шаге ``images_main``.
    ``prev`` — как после ``structure_main``: ``{"run_id", "step_id"}`` (``step_id`` — stub).
    """
    run_id = str(prev["run_id"])
    stub_step_id = str(prev["step_id"])

    db = _session()
    try:
        run = db.scalars(select(PipelineRun).where(PipelineRun.id == run_id)).one_or_none()
        if run is None:
            raise ValueError(f"images_main: run not found run_id={run_id}")

        stub_step = db.scalars(select(PipelineStep).where(PipelineStep.id == stub_step_id)).one_or_none()
        if stub_step is None or stub_step.run_id != run.id:
            raise ValueError("images_main: stub step mismatch")

        st_step = db.scalars(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == STRUCTURE_STEP_KEY,
            )
        ).one_or_none()
        if st_step is None or st_step.status != "done":
            raise ValueError("images_main: structure_main not done")

        structure = _structure_result_from_step(st_step)
        if structure is None:
            msg = "images_main: invalid structure meta_json"
            raise ValueError(msg)

        img_step = db.scalars(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == IMAGES_MAIN_STEP_KEY,
            )
        ).one_or_none()
        if img_step is None:
            raise ValueError("images_main: images_main step missing")

        if img_step.status == "done" and _count_main_frame_assets(db, step_id=img_step.id) >= 4:
            logger.info("wip_images_main: idempotent skip run_id=%s", run_id)
            return {"run_id": run_id, "step_id": stub_step_id}

        if img_step.status == "failed":
            raise RuntimeError(
                f"images_main: step already failed run_id={run_id} err={img_step.error_message!r}"
            )

        n_existing = _count_main_frame_assets(db, step_id=img_step.id)
        if 0 < n_existing < 4:
            logger.info(
                "wip_images_main: partial assets=%s run_id=%s, wiping and regenerating",
                n_existing,
                run_id,
            )
            _wipe_step_assets(db, step_id=img_step.id, run_id=run_id)
            db.commit()
            db.refresh(img_step)

        img_step.status = "running"
        img_step.error_message = None
        db.add(img_step)
        db.commit()
        db.refresh(img_step)

        media_root = _media_root()
        run_dir = media_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        prompts = structure.main_prompts
        if len(prompts) != 4:
            msg = f"images_main: expected 4 prompts, got {len(prompts)}"
            img_step.status = "failed"
            img_step.error_message = msg
            run.status = "failed"
            db.add(img_step)
            db.add(run)
            db.commit()
            raise ValueError(msg)

        model_name = (os.getenv("WIP_OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()

        existing_by_index: dict[int, PipelineAsset] = {}
        for a in db.scalars(select(PipelineAsset).where(PipelineAsset.step_id == img_step.id)).all():
            meta = a.meta_json if isinstance(a.meta_json, dict) else {}
            idx = meta.get("frame_index")
            if isinstance(idx, int) and 0 <= idx <= 3:
                existing_by_index[idx] = a

        payload = dict(run.payload_json or {})
        reference_asset_ids = [
            str(v).strip()
            for v in payload.get("reference_asset_ids", [])
            if str(v).strip()
        ]

        try:
            refs = fetch_reference_images(
                monolith_job_id=str(run.monolith_job_id or ""),
                reference_asset_ids=reference_asset_ids,
            )
            refs_meta = reference_metadata(refs)
            reference_fingerprint = hashlib.sha256(
                "|".join(r.sha256_hex for r in refs).encode("utf-8")
            ).hexdigest()
            for idx, prompt in enumerate(prompts):
                if idx in existing_by_index:
                    rel = existing_by_index[idx].storage_rel_path
                    fp = (media_root / rel).resolve()
                    if fp.is_file() and _file_under_media(fp, media_root):
                        logger.info("wip_images_main: skip frame idx=%s (exists)", idx)
                        continue

                image_prompt = build_main_image_prompt(prompt)
                raw, mime = call_openai_image_bytes(prompt=image_prompt, reference_images=refs)
                digest = hashlib.sha256(raw).hexdigest()
                rel_path = f"{run_id}/main_frame_{idx}.png"
                out_path = media_root / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(raw)

                if idx in existing_by_index:
                    old = existing_by_index[idx]
                    db.delete(old)
                    db.flush()

                asset = PipelineAsset(
                    run_id=run.id,
                    step_id=img_step.id,
                    kind="main_frame",
                    storage_rel_path=rel_path,
                    mime_type=mime,
                    sha256_hex=digest,
                    meta_json={
                        "frame_index": idx,
                        "openai_image_model": model_name,
                        "prompt": prompt,
                        "image_prompt": image_prompt,
                        "reference_asset_ids": reference_asset_ids,
                        "reference_fingerprint": reference_fingerprint,
                        "reference_images": refs_meta,
                    },
                )
                db.add(asset)
                db.flush()
        except Exception as exc:
            logger.exception("wip_images_main: generation failed run_id=%s", run_id)
            img_step.status = "failed"
            img_step.error_message = str(exc)[:2000]
            run.status = "failed"
            db.add(img_step)
            db.add(run)
            db.commit()
            raise

        if _count_main_frame_assets(db, step_id=img_step.id) < 4:
            msg = "images_main: fewer than 4 assets after generation"
            img_step.status = "failed"
            img_step.error_message = msg
            run.status = "failed"
            db.add(img_step)
            db.add(run)
            db.commit()
            raise RuntimeError(msg)

        img_step.status = "done"
        img_step.error_message = None
        db.add(img_step)
        db.commit()

        logger.info("wip_images_main: done run_id=%s step_id=%s", run_id, img_step.id)
        return {"run_id": run_id, "step_id": stub_step_id}
    finally:
        db.close()
