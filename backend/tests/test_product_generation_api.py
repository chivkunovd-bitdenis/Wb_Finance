from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.dependencies import get_current_user
from app.main import app
from app.models.product_generation_job import ProductGenerationJob
from app.models.user import User

ADMIN_PG_ID = "00000000-0000-0000-0000-0000000000a1"
USER_PG_ID = "00000000-0000-0000-0000-0000000000a2"


@pytest.fixture(scope="module", autouse=True)
def _ensure_product_generation_table() -> None:
    ProductGenerationJob.__table__.create(bind=engine, checkfirst=True)


def _ensure_user(*, user_id: str, is_admin: bool) -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.id == user_id).first()
        if not existing:
            db.add(
                User(
                    id=user_id,
                    email=f"{user_id}@pg-test.local",
                    password_hash="x",
                    wb_api_key=None,
                    is_admin=is_admin,
                    is_active=True,
                )
            )
            db.commit()
        else:
            existing.is_admin = is_admin
            db.add(existing)
            db.commit()
    finally:
        db.close()


def _cleanup_jobs(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(ProductGenerationJob).filter(ProductGenerationJob.user_id == user_id).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client_admin() -> Generator[TestClient, None, None]:
    _ensure_user(user_id=ADMIN_PG_ID, is_admin=True)
    _cleanup_jobs(ADMIN_PG_ID)

    app.dependency_overrides[get_current_user] = lambda: MagicMock(
        id=ADMIN_PG_ID,
        is_active=True,
        is_admin=True,
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    _cleanup_jobs(ADMIN_PG_ID)


@pytest.fixture
def client_non_admin() -> Generator[TestClient, None, None]:
    _ensure_user(user_id=USER_PG_ID, is_admin=False)

    app.dependency_overrides[get_current_user] = lambda: MagicMock(
        id=USER_PG_ID,
        is_active=True,
        is_admin=False,
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def test_product_generation_requires_auth(client: TestClient) -> None:
    r = client.get("/ai/product-generation/jobs")
    assert r.status_code == 401


def test_product_generation_forbidden_for_non_admin(client_non_admin: TestClient) -> None:
    r = client_non_admin.get("/ai/product-generation/jobs")
    assert r.status_code == 403
    r2 = client_non_admin.post("/ai/product-generation/jobs", json={})
    assert r2.status_code == 403


def test_product_generation_crud_happy_path(client_admin: TestClient) -> None:
    r = client_admin.post("/ai/product-generation/jobs", json={})
    assert r.status_code == 201
    job_id = r.json()["id"]
    assert r.json()["status"] == "draft"
    assert r.json()["user_id"] == ADMIN_PG_ID

    r2 = client_admin.post(
        "/ai/product-generation/jobs",
        json={
            "title": "Платье",
            "brand": "TestBrand",
            "vendor_code": "VC-1",
            "price_kopeks": 199900,
            "sizes": [{"tech_size": "M", "wb_size": "48"}],
        },
    )
    assert r2.status_code == 201
    assert r2.json()["title"] == "Платье"
    assert r2.json()["sizes_json"] == [{"tech_size": "M", "wb_size": "48"}]

    r3 = client_admin.get("/ai/product-generation/jobs")
    assert r3.status_code == 200
    assert len(r3.json()["items"]) >= 2

    r4 = client_admin.get(f"/ai/product-generation/jobs/{job_id}")
    assert r4.status_code == 200
    assert r4.json()["id"] == job_id

    r5 = client_admin.patch(
        f"/ai/product-generation/jobs/{job_id}",
        json={"seo_description": "SEO here", "status": "in_progress"},
    )
    assert r5.status_code == 200
    assert r5.json()["seo_description"] == "SEO here"
    assert r5.json()["status"] == "in_progress"


def test_product_generation_wb_subject_id_optional_and_patch(client_admin: TestClient) -> None:
    r = client_admin.post("/ai/product-generation/jobs", json={"title": "X", "brand": "B", "vendor_code": "v"})
    assert r.status_code == 201
    job_id = r.json()["id"]
    assert r.json().get("wb_subject_id") is None

    r2 = client_admin.post(
        "/ai/product-generation/jobs",
        json={
            "title": "Y",
            "brand": "B",
            "vendor_code": "v2",
            "wb_subject_id": 105,
        },
    )
    assert r2.status_code == 201
    assert r2.json().get("wb_subject_id") == 105

    r3 = client_admin.patch(f"/ai/product-generation/jobs/{job_id}", json={"wb_subject_id": 200})
    assert r3.status_code == 200
    assert r3.json().get("wb_subject_id") == 200

    r4 = client_admin.patch(f"/ai/product-generation/jobs/{job_id}", json={"wb_subject_id": None})
    assert r4.status_code == 200
    assert r4.json().get("wb_subject_id") is None


def test_product_generation_wb_subject_id_zero_rejected(client_admin: TestClient) -> None:
    r = client_admin.post("/ai/product-generation/jobs", json={"wb_subject_id": 0})
    assert r.status_code == 422


def test_product_generation_patch_invalid_status(client_admin: TestClient) -> None:
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    bad = client_admin.patch(f"/ai/product-generation/jobs/{job_id}", json={"status": "nope"})
    assert bad.status_code == 400


def test_product_generation_reference_upload_and_download(client_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={"title": "Ref job"})
    assert r.status_code == 201
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    r2 = client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("shot.png", png, "image/png"))],
    )
    assert r2.status_code == 200
    body = r2.json()
    refs = body.get("reference_paths_json") or []
    assert len(refs) == 1
    assert refs[0].get("asset_id")
    assert refs[0].get("stored_name", "").endswith(".png")
    aid = str(refs[0]["asset_id"])
    r3 = client_admin.get(f"/ai/product-generation/jobs/{job_id}/references/{aid}/file")
    assert r3.status_code == 200
    assert r3.content.startswith(b"\x89PNG")


def test_product_generation_reference_rejects_non_image(client_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    bad = client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("x.txt", b"hello", "text/plain"))],
    )
    assert bad.status_code == 415


def test_product_generation_reference_upload_only_draft(client_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    client_admin.patch(f"/ai/product-generation/jobs/{job_id}", json={"status": "in_progress"})
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    blocked = client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    assert blocked.status_code == 400


def test_product_generation_reference_download_unknown_asset(client_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    nf = client_admin.get(f"/ai/product-generation/jobs/{job_id}/references/deadbeef/file")
    assert nf.status_code == 404


def test_product_generation_not_found_for_other_user(client_admin: TestClient) -> None:
    other_id = "00000000-0000-0000-0000-0000000000b1"
    _ensure_user(user_id=other_id, is_admin=True)
    db = SessionLocal()
    try:
        job = ProductGenerationJob(user_id=other_id, status="draft")
        db.add(job)
        db.commit()
        db.refresh(job)
        foreign_id = str(job.id)
    finally:
        db.close()
    try:
        r = client_admin.get(f"/ai/product-generation/jobs/{foreign_id}")
        assert r.status_code == 404
    finally:
        db2 = SessionLocal()
        try:
            db2.query(ProductGenerationJob).filter(ProductGenerationJob.user_id == other_id).delete()
            db2.commit()
        finally:
            db2.close()


def test_product_generation_start_pipeline_sets_in_progress(client_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={"title": "Pipeline job"})
    assert r.status_code == 201
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    up = client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    assert up.status_code == 200
    rs = client_admin.post(f"/ai/product-generation/jobs/{job_id}/start")
    assert rs.status_code == 200
    body = rs.json()
    assert body["status"] == "in_progress"
    assert body["pipeline_run_id"] and str(body["pipeline_run_id"]).startswith("local-")
    rlist = client_admin.get("/ai/product-generation/jobs")
    assert rlist.status_code == 200
    row = next(item for item in rlist.json()["items"] if item["id"] == job_id)
    assert row["status"] == "in_progress"


def test_product_generation_start_requires_references(client_admin: TestClient) -> None:
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    rs = client_admin.post(f"/ai/product-generation/jobs/{job_id}/start")
    assert rs.status_code == 400


def test_product_generation_start_only_once(client_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    assert client_admin.post(f"/ai/product-generation/jobs/{job_id}/start").status_code == 200
    again = client_admin.post(f"/ai/product-generation/jobs/{job_id}/start")
    assert again.status_code == 400


def test_product_generation_start_celery_enqueue_failure_reverts(
    client_admin: TestClient, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    with patch("celery_app.tasks.product_generation_pipeline_stub.delay") as mock_delay:
        mock_delay.side_effect = RuntimeError("broker down")
        rs = client_admin.post(f"/ai/product-generation/jobs/{job_id}/start")
    assert rs.status_code == 503
    rg = client_admin.get(f"/ai/product-generation/jobs/{job_id}")
    assert rg.status_code == 200
    assert rg.json()["status"] == "draft"
    assert rg.json()["pipeline_run_id"] is None


def test_product_generation_start_remote_image_pipeline(
    client_admin: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL", "http://wip.test")
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_SECRET", "secret-for-test")
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def fake_post(url: str, **_kwargs: object) -> httpx.Response:
        assert url == "http://wip.test/internal/v1/runs"
        return httpx.Response(201, json={"id": run_uuid, "status": "created"})

    def fake_get(url: str, **_kwargs: object) -> httpx.Response:
        assert run_uuid in url
        return httpx.Response(
            200,
            json={"status": "running", "steps": [], "updated_at": "2026-05-13T12:00:00Z"},
        )

    r = client_admin.post("/ai/product-generation/jobs", json={"title": "Remote"})
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    with patch("app.services.product_generation_image_pipeline.httpx.post", side_effect=fake_post) as m_post:
        with patch("app.services.product_generation_image_pipeline.httpx.get", side_effect=fake_get):
            with patch("celery_app.tasks.product_generation_pipeline_stub.delay") as mock_delay:
                rs = client_admin.post(f"/ai/product-generation/jobs/{job_id}/start")
    assert rs.status_code == 200
    body = rs.json()
    assert body["pipeline_run_id"] == run_uuid
    assert not str(body["pipeline_run_id"]).startswith("local-")
    mock_delay.assert_not_called()
    m_post.assert_called_once()


def test_product_generation_start_remote_image_pipeline_503_on_http_error(
    client_admin: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL", "http://wip.test")
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_SECRET", "secret-for-test")
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))

    def fake_post(_url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(503, text="no")

    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    with patch("app.services.product_generation_image_pipeline.httpx.post", side_effect=fake_post):
        rs = client_admin.post(f"/ai/product-generation/jobs/{job_id}/start")
    assert rs.status_code == 503
    rg = client_admin.get(f"/ai/product-generation/jobs/{job_id}")
    assert rg.json()["status"] == "draft"


def test_product_generation_list_includes_image_pipeline_snapshot(
    client_admin: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL", "http://wip.test")
    monkeypatch.setenv("PRODUCT_GEN_IMAGE_PIPELINE_SECRET", "secret-for-test")
    monkeypatch.setenv("PRODUCT_GENERATION_REFERENCES_DIR", str(tmp_path))
    run_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    def fake_post(url: str, **_kwargs: object) -> httpx.Response:
        assert url == "http://wip.test/internal/v1/runs"
        return httpx.Response(201, json={"id": run_uuid, "status": "created"})

    def fake_get(url: str, **_kwargs: object) -> httpx.Response:
        assert url == f"http://wip.test/internal/v1/runs/{run_uuid}"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "updated_at": "2026-05-13T12:00:00Z",
                "steps": [{"step_key": "pg32_stub", "status": "done", "ordinal": 0}],
            },
        )

    r = client_admin.post("/ai/product-generation/jobs", json={"title": "Poll"})
    job_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    client_admin.post(
        f"/ai/product-generation/jobs/{job_id}/references",
        files=[("files", ("a.png", png, "image/png"))],
    )
    with patch("app.services.product_generation_image_pipeline.httpx.post", side_effect=fake_post):
        assert client_admin.post(f"/ai/product-generation/jobs/{job_id}/start").status_code == 200

    with patch("app.services.product_generation_image_pipeline.httpx.get", side_effect=fake_get):
        rlist = client_admin.get("/ai/product-generation/jobs")
    assert rlist.status_code == 200
    row = next(x for x in rlist.json()["items"] if x["id"] == job_id)
    assert row.get("image_pipeline") is not None
    assert row["image_pipeline"]["remote_status"] == "completed"
    assert len(row["image_pipeline"]["steps"]) == 1
