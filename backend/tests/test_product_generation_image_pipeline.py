from __future__ import annotations

from app.models.product_generation_job import ProductGenerationJob
from app.services.product_generation_image_pipeline import build_image_pipeline_payload


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
