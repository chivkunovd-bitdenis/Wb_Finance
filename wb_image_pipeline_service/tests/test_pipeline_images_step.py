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
        run = PipelineRun(
            status="created",
            monolith_job_id="job-1",
            payload_json={"reference_asset_ids": ["x"]},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    prev = apply_run_created(run_id)
    with patch("app.services.pipeline_structure_step.call_structure_main_model", return_value=fake):
        mid = apply_structure_main_step(prev)

    with patch(
        "app.services.pipeline_images_step.fetch_reference_images",
        return_value=[_fake_reference()],
    ), patch(
        "app.services.pipeline_images_step.call_openai_image_bytes",
        return_value=(_MINI_PNG, "image/png"),
    ) as m_img:
        out1 = apply_images_main_step(mid)
        out2 = apply_images_main_step(mid)
    assert m_img.call_count == 4
    first_call = m_img.call_args_list[0].kwargs
    assert first_call["reference_images"][0].asset_id == "x"
    assert out1 == out2
    assert out1["run_id"] == run_id

    db = SessionLocal()
    try:
        from app.models.pipeline import PipelineAsset

        assets = db.query(PipelineAsset).filter(PipelineAsset.run_id == run_id).all()
        assert len(assets) == 4
        assert {a.meta_json.get("prompt") for a in assets if isinstance(a.meta_json, dict)} == {"a", "b", "c", "d"}
        first_meta = assets[0].meta_json or {}
        assert first_meta["reference_asset_ids"] == ["x"]
        assert first_meta["reference_images"][0]["asset_id"] == "x"
        assert first_meta["reference_fingerprint"]
    finally:
        db.close()


def test_apply_images_main_fails_without_reference_file(images_db: None) -> None:
    from app.db import SessionLocal
    from app.models.pipeline import PipelineRun, PipelineStep
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

    with pytest.raises(ValueError, match="monolith_job_id"):
        apply_images_main_step(mid)

    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
        step = (
            db.query(PipelineStep)
            .filter(PipelineStep.run_id == run_id)
            .filter(PipelineStep.step_key == "images_main")
            .one()
        )
        assert run.status == "failed"
        assert step.status == "failed"
        assert "monolith_job_id" in str(step.error_message)
    finally:
        db.close()
