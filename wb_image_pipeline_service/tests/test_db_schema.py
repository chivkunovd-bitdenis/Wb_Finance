from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

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


def test_alembic_creates_tables_and_orm_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'wip.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    sys.path.insert(0, str(_SVC_ROOT))

    _run_alembic_upgrade(db_url)
    _reload_config_and_db()

    from app.db import SessionLocal
    from app.models.pipeline import PipelineAsset, PipelineRun, PipelineStep

    db = SessionLocal()
    try:
        run = PipelineRun(status="created", monolith_job_id="job-uuid-1", payload_json={"k": 1})
        db.add(run)
        db.flush()
        step = PipelineStep(run_id=run.id, step_key="structure", ordinal=0, status="pending")
        db.add(step)
        db.flush()
        asset = PipelineAsset(
            run_id=run.id,
            step_id=step.id,
            kind="generated_image",
            storage_rel_path="runs/x/a.png",
            mime_type="image/png",
        )
        db.add(asset)
        db.commit()

        loaded = db.query(PipelineRun).filter(PipelineRun.id == run.id).one()
        assert loaded.monolith_job_id == "job-uuid-1"
        assert len(loaded.steps) == 1
        assert len(loaded.assets) == 1
        assert loaded.assets[0].storage_rel_path.endswith("a.png")

        db.delete(loaded)
        db.commit()
        assert db.query(PipelineStep).count() == 0
        assert db.query(PipelineAsset).count() == 0
    finally:
        db.close()


def test_check_constraint_rejects_bad_run_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'wip2.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    sys.path.insert(0, str(_SVC_ROOT))

    _run_alembic_upgrade(db_url)
    _reload_config_and_db()

    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun

    db = SessionLocal()
    try:
        db.add(PipelineRun(status="not-a-valid-status"))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
