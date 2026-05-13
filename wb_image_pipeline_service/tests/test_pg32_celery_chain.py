from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


@pytest.fixture
def pg32_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    db_url = f"sqlite:///{tmp_path / 'wip_pg32.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    monkeypatch.setenv("WIP_REDIS_URL", "redis://127.0.0.1:6379/15")
    sys.path.insert(0, str(_SVC_ROOT))

    _run_alembic_upgrade(db_url)
    _reload_config_and_db()

    import celery_app.celery_app as cap
    import celery_app.pipeline_tasks as pt

    importlib.reload(cap)
    importlib.reload(pt)

    return db_url


def test_pg32_chain_eager_completes_run(pg32_db: str) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun, PipelineStep
    from celery import chain
    from celery_app.celery_app import celery_app
    from celery_app.pipeline_tasks import run_created, step_done, structure_main

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    db = SessionLocal()
    try:
        run = PipelineRun(
            status="created",
            monolith_job_id=None,
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
        "app.services.pipeline_structure_step.call_structure_main_model",
        return_value=_fake_structure_result(),
    ):
        chain(run_created.s(run_id), structure_main.s(), step_done.s()).apply_async().get(timeout=10)

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
        assert len(steps) == 2
        assert steps[0].step_key == "structure_main"
        assert steps[0].status == "done"
        assert steps[0].meta_json is not None
        assert steps[0].meta_json.get("seo_title") == "Title"
        assert len(steps[0].meta_json.get("main_prompts") or []) == 4
        assert steps[1].step_key == "pg32_stub"
        assert steps[1].status == "done"
    finally:
        db.close()


def test_pg32_apply_run_created_idempotent(pg32_db: str) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun, PipelineStep
    from app.services.pipeline_pg32_stub import apply_run_created

    db = SessionLocal()
    try:
        run = PipelineRun(status="created")
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
        assert db.query(PipelineStep).filter(PipelineStep.run_id == run_id).count() == 2
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
    from app.services.pipeline_structure_step import apply_structure_main_step

    db = SessionLocal()
    try:
        run = PipelineRun(status="created")
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    payload = apply_run_created(run_id)
    with patch(
        "app.services.pipeline_structure_step.call_structure_main_model",
        return_value=_fake_structure_result(),
    ):
        mid = apply_structure_main_step(payload)
    apply_step_done(mid)
    apply_step_done(mid)

    db = SessionLocal()
    try:
        r = db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
        assert r.status == "completed"
        steps = {s.step_key: s for s in db.query(PipelineStep).filter(PipelineStep.run_id == run_id).all()}
        assert steps["structure_main"].status == "done"
        assert steps["pg32_stub"].status == "done"
    finally:
        db.close()
