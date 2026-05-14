from __future__ import annotations

import base64
import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_SVC_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {**os.environ, "WIP_DATABASE_URL": db_url, "PYTHONPATH": str(_SVC_ROOT)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_SVC_ROOT,
        env=env,
        check=True,
    )


def _reload_config_and_db() -> None:
    import app.config as cfg
    import app.db as dbm

    importlib.reload(cfg)
    importlib.reload(dbm)


def _fake_structure_result():
    from app.schemas.structure_main import StructureMainResult

    return StructureMainResult(
        seo_title="Title",
        seo_description="Desc " * 30,
        main_prompts=["p1", "p2", "p3", "p4"],
    )


def _fake_reference() -> object:
    from app.services.reference_fetch_client import ReferenceImage

    return ReferenceImage(
        asset_id="r1",
        filename="r1.png",
        mime_type="image/png",
        content=b"reference",
        sha256_hex="ref-sha",
    )


@pytest.fixture
def pg32_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    db_url = f"sqlite:///{tmp_path / 'wip_pg32.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    monkeypatch.setenv("WIP_REDIS_URL", "redis://127.0.0.1:6379/15")
    monkeypatch.setenv("WIP_MEDIA_ROOT", str(tmp_path / "wip_media"))
    sys.path.insert(0, str(_SVC_ROOT))

    _run_alembic_upgrade(db_url)
    _reload_config_and_db()

    import celery_app.celery_app as cap
    import celery_app.pipeline_tasks as pt

    importlib.reload(cap)
    importlib.reload(pt)

    return db_url


def test_pg32_chain_eager_completes_run(pg32_db: str, tmp_path: Path) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineAsset, PipelineRun, PipelineStep
    from celery import chain
    from celery_app.celery_app import celery_app
    from celery_app.pipeline_tasks import images_main, run_created, step_done, structure_main

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    db = SessionLocal()
    try:
        run = PipelineRun(
            status="created",
            monolith_job_id="job-eager",
            payload_json={
                "reference_asset_ids": ["r1"],
                "description_user": "Тестовое описание",
                "title": None,
            },
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    with patch(
        "app.services.pipeline_structure_step.fetch_reference_images",
        return_value=[_fake_reference()],
    ), patch(
        "app.services.pipeline_structure_step.call_structure_main_model",
        return_value=_fake_structure_result(),
    ), patch(
        "app.services.pipeline_images_step.fetch_reference_images",
        return_value=[_fake_reference()],
    ), patch(
        "app.services.pipeline_images_step.call_openai_image_bytes",
        return_value=(_MINI_PNG, "image/png"),
    ):
        chain(
            run_created.s(run_id),
            structure_main.s(),
            images_main.s(),
            step_done.s(),
        ).apply_async().get(timeout=10)

    db = SessionLocal()
    try:
        r = db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
        assert r.status == "completed"
        assert r.payload_json is not None
        assert "wip_effective_image_prompt" in r.payload_json
        assert "Тестовое описание" in str(r.payload_json["wip_effective_image_prompt"])
        assert r.payload_json.get("wip_prompt_template_version")
        steps = sorted(
            db.query(PipelineStep).filter(PipelineStep.run_id == run_id).all(),
            key=lambda s: s.ordinal,
        )
        assert len(steps) == 3
        assert steps[0].step_key == "structure_main"
        assert steps[0].status == "done"
        assert steps[0].meta_json is not None
        assert steps[0].meta_json.get("seo_title") == "Title"
        assert len(steps[0].meta_json.get("main_prompts") or []) == 4
        assert steps[1].step_key == "images_main"
        assert steps[1].status == "done"
        assert steps[2].step_key == "pg32_stub"
        assert steps[2].status == "done"
        assets = db.query(PipelineAsset).filter(PipelineAsset.run_id == run_id).all()
        assert len(assets) == 4
        assert {a.kind for a in assets} == {"main_frame"}
        for a in assets:
            p = Path(tmp_path / "wip_media") / a.storage_rel_path
            assert p.is_file()
    finally:
        db.close()


def test_pg32_apply_run_created_idempotent(pg32_db: str) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun, PipelineStep
    from app.services.pipeline_pg32_stub import apply_run_created

    db = SessionLocal()
    try:
        run = PipelineRun(
            status="created",
            monolith_job_id="job-step-done",
            payload_json={"reference_asset_ids": ["r1"]},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    out1 = apply_run_created(run_id)
    out2 = apply_run_created(run_id)
    assert out1["run_id"] == out2["run_id"]
    assert out1["step_id"] == out2["step_id"]

    db = SessionLocal()
    try:
        assert db.query(PipelineStep).filter(PipelineStep.run_id == run_id).count() == 3
        r = db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
        assert r.status == "running"
        assert r.payload_json is not None
        assert "wip_effective_image_prompt" in r.payload_json
    finally:
        db.close()


def test_pg32_apply_step_done_idempotent(pg32_db: str) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun, PipelineStep
    from app.services.pipeline_pg32_stub import apply_run_created, apply_step_done
    from app.services.pipeline_images_step import apply_images_main_step
    from app.services.pipeline_structure_step import apply_structure_main_step

    db = SessionLocal()
    try:
        run = PipelineRun(
            status="created",
            monolith_job_id="job-step-done",
            payload_json={"reference_asset_ids": ["r1"]},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    payload = apply_run_created(run_id)
    with patch(
        "app.services.pipeline_structure_step.fetch_reference_images",
        return_value=[_fake_reference()],
    ), patch(
        "app.services.pipeline_structure_step.call_structure_main_model",
        return_value=_fake_structure_result(),
    ), patch(
        "app.services.pipeline_images_step.fetch_reference_images",
        return_value=[_fake_reference()],
    ), patch(
        "app.services.pipeline_images_step.call_openai_image_bytes",
        return_value=(_MINI_PNG, "image/png"),
    ):
        mid = apply_structure_main_step(payload)
        mid = apply_images_main_step(mid)
    apply_step_done(mid)
    apply_step_done(mid)

    db = SessionLocal()
    try:
        r = db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
        assert r.status == "completed"
        steps = {s.step_key: s for s in db.query(PipelineStep).filter(PipelineStep.run_id == run_id).all()}
        assert steps["structure_main"].status == "done"
        assert steps["images_main"].status == "done"
        assert steps["pg32_stub"].status == "done"
    finally:
        db.close()


def test_pg32_migrate_legacy_structure_stub_adds_images_step(pg32_db: str) -> None:
    """PG-B.2 → B.3: у run только structure+stub — apply_run_created вставляет images_main."""
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun, PipelineStep
    from app.services.pipeline_pg32_stub import (
        STRUCTURE_STEP_KEY,
        STUB_STEP_KEY,
        apply_run_created,
    )

    db = SessionLocal()
    try:
        run = PipelineRun(status="created", payload_json={"reference_asset_ids": ["r"]})
        db.add(run)
        db.flush()
        st = PipelineStep(
            run_id=run.id,
            step_key=STRUCTURE_STEP_KEY,
            ordinal=0,
            status="pending",
        )
        stub = PipelineStep(
            run_id=run.id,
            step_key=STUB_STEP_KEY,
            ordinal=1,
            status="pending",
        )
        db.add(st)
        db.add(stub)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    apply_run_created(run_id)

    db = SessionLocal()
    try:
        steps = sorted(
            db.query(PipelineStep).filter(PipelineStep.run_id == run_id).all(),
            key=lambda s: s.ordinal,
        )
        assert [s.step_key for s in steps] == ["structure_main", "images_main", "pg32_stub"]
        assert [s.ordinal for s in steps] == [0, 1, 2]
    finally:
        db.close()
