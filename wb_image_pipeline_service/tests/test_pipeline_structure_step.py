from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SVC_ROOT = Path(__file__).resolve().parents[1]


def _fake_reference() -> object:
    from app.services.reference_fetch_client import ReferenceImage

    return ReferenceImage(
        asset_id="x",
        filename="x.png",
        mime_type="image/png",
        content=b"reference",
        sha256_hex="ref-sha",
    )


def _run_alembic_upgrade(db_url: str) -> None:
    env = {**os.environ, "WIP_DATABASE_URL": db_url, "PYTHONPATH": str(_SVC_ROOT)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_SVC_ROOT,
        env=env,
        check=True,
    )


@pytest.fixture
def structure_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'wip_structure.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    sys.path.insert(0, str(_SVC_ROOT))
    _run_alembic_upgrade(db_url)
    import app.config as cfg
    import app.db as dbm

    importlib.reload(cfg)
    importlib.reload(dbm)


def test_apply_structure_main_idempotent(structure_db: None) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun
    from app.schemas.structure_main import StructureMainResult
    from app.services.pipeline_pg32_stub import apply_run_created
    from app.services.pipeline_structure_step import apply_structure_main_step

    fake = StructureMainResult(
        seo_title="T",
        seo_description="D " * 30,
        main_prompts=["a", "b", "c", "d"],
    )

    db = SessionLocal()
    try:
        run = PipelineRun(
            status="created",
            monolith_job_id="job-structure",
            payload_json={"reference_asset_ids": ["x"]},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    prev = apply_run_created(run_id)
    with patch(
        "app.services.pipeline_structure_step.fetch_reference_images",
        return_value=[_fake_reference()],
    ), patch("app.services.pipeline_structure_step.call_structure_main_model", return_value=fake) as m:
        out1 = apply_structure_main_step(prev)
        out2 = apply_structure_main_step(prev)
    m.assert_called_once()
    assert m.call_args.kwargs["reference_images"][0].asset_id == "x"

    assert out1 == out2
    assert out1["run_id"] == run_id
