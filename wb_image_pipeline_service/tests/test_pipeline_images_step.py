"""PG-B.3: шаг images_main — идемпотентность и 4 ассета."""

from __future__ import annotations

import base64
import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SVC_ROOT = Path(__file__).resolve().parents[1]
_MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
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
def images_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'wip_images.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    monkeypatch.setenv("WIP_MEDIA_ROOT", str(tmp_path / "wip_media"))
    sys.path.insert(0, str(_SVC_ROOT))
    _run_alembic_upgrade(db_url)
    import app.config as cfg
    import app.db as dbm

    importlib.reload(cfg)
    importlib.reload(dbm)


def test_apply_images_main_idempotent(images_db: None) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun
    from app.schemas.structure_main import StructureMainResult
    from app.services.pipeline_images_step import apply_images_main_step
    from app.services.pipeline_pg32_stub import apply_run_created
    from app.services.pipeline_structure_step import apply_structure_main_step

    fake = StructureMainResult(
        seo_title="T",
        seo_description="D " * 30,
        main_prompts=["a", "b", "c", "d"],
    )

    db = SessionLocal()
    try:
        run = PipelineRun(status="created", payload_json={"reference_asset_ids": ["x"]})
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    prev = apply_run_created(run_id)
    with patch("app.services.pipeline_structure_step.call_structure_main_model", return_value=fake):
        mid = apply_structure_main_step(prev)

    with patch(
        "app.services.pipeline_images_step.call_openai_image_bytes",
        return_value=(_MINI_PNG, "image/png"),
    ) as m_img:
        out1 = apply_images_main_step(mid)
        out2 = apply_images_main_step(mid)
    assert m_img.call_count == 4
    assert out1 == out2
    assert out1["run_id"] == run_id
