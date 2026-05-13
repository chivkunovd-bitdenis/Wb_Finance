from __future__ import annotations

from app.models.product_generation_job import ProductGenerationJob
from app.services.product_generation_image_pipeline import (
    build_image_pipeline_payload,
    build_image_pipeline_timeline,
)


def test_build_image_pipeline_payload_minimal_image_phase() -> None:
    """PG-A.1: payload для image-run не требует полей карточки — только reference_asset_ids + текст."""
    job = ProductGenerationJob(
        user_id="00000000-0000-0000-0000-000000000099",
        status="draft",
        description_user="Пользовательский текст",
        reference_paths_json=[{"asset_id": "asset-1", "stored_name": "a.png"}],
    )
    out = build_image_pipeline_payload(job)
    assert out["reference_asset_ids"] == ["asset-1"]
    assert out["description_user"] == "Пользовательский текст"
    assert out["title"] is None
    assert out["vendor_code"] is None
    assert out["brand"] is None
    assert out["wb_subject_id"] is None
    assert out["seo_description"] is None
    assert out["price_kopeks"] is None
    assert out["sizes_json"] is None
    assert out["dimensions_length"] is None


def test_build_image_pipeline_timeline_failed_step_includes_error() -> None:
    remote = {
        "id": "run-u1",
        "status": "failed",
        "updated_at": "2026-05-13T12:00:00Z",
        "monolith_job_id": "job-abc",
        "steps": [
            {
                "step_key": "structure_main",
                "status": "failed",
                "ordinal": 0,
                "error_message": "OpenAI HTTP 403",
            },
        ],
    }
    tl = build_image_pipeline_timeline(remote)
    assert len(tl) >= 2
    assert tl[0]["level"] == "error"
    assert any("OpenAI HTTP 403" in e["body"] for e in tl)


def test_build_image_pipeline_timeline_running_images_hint() -> None:
    remote = {
        "id": "r2",
        "status": "running",
        "steps": [{"step_key": "images_main", "status": "running", "ordinal": 1}],
    }
    tl = build_image_pipeline_timeline(remote)
    img = next((e for e in tl if "изображен" in e["title"].lower()), None)
    assert img is not None
    assert "OpenAI" in img["body"]


def test_build_image_pipeline_timeline_empty_steps_adds_explanation() -> None:
    remote = {
        "id": "r-empty",
        "status": "completed",
        "updated_at": "2026-05-13T18:00:00Z",
        "steps": [],
    }
    tl = build_image_pipeline_timeline(remote)
    assert len(tl) >= 2
    assert any("steps" in e["body"].lower() for e in tl)


def test_enrich_job_out_splits_main_and_content_assets(monkeypatch) -> None:
    from app.schemas.product_generation import ProductGenerationJobOut
    from app.services.product_generation_image_pipeline import enrich_job_out_with_image_pipeline

    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL", "http://wip.test")
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_SECRET", "secret")

    def fake_fetch(_run_id: str) -> dict:
        return {
            "status": "completed",
            "steps": [],
            "assets": [
                {
                    "id": "main-1",
                    "kind": "main_frame",
                    "mime_type": "image/png",
                    "meta_json": {"frame_index": 0, "prompt": "main"},
                },
                {
                    "id": "content-1",
                    "kind": "content_frame",
                    "mime_type": "image/png",
                    "meta_json": {"series_index": 0, "prompt": "content", "selected_main_asset_id": "main-1"},
                },
            ],
        }

    monkeypatch.setattr("app.services.product_generation_image_pipeline.fetch_remote_run", fake_fetch)
    out = ProductGenerationJobOut(
        id="job-1",
        user_id="user-1",
        status="ready_to_publish",
        pipeline_run_id="run-1",
        vendor_code=None,
        title=None,
        brand=None,
        wb_subject_id=None,
        description_user=None,
        seo_description=None,
        price_kopeks=None,
        dimensions_length=None,
        dimensions_width=None,
        dimensions_height=None,
        weight_brutto=None,
        sizes_json=None,
        reference_paths_json=None,
        selected_main_asset_id=None,
        selected_series_asset_ids=None,
        wb_publish_error=None,
        wb_response_json=None,
        created_at="2026-05-13T12:00:00Z",
        updated_at="2026-05-13T12:00:00Z",
    )

    enriched = enrich_job_out_with_image_pipeline(out)
    assert enriched.image_pipeline is not None
    assert enriched.image_pipeline["generated_assets"][0]["asset_id"] == "main-1"
    assert enriched.image_pipeline["content_assets"][0]["asset_id"] == "content-1"
    assert enriched.image_pipeline["content_assets"][0]["selected_main_asset_id"] == "main-1"
