from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

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


def test_product_generation_patch_invalid_status(client_admin: TestClient) -> None:
    r = client_admin.post("/ai/product-generation/jobs", json={})
    job_id = r.json()["id"]
    bad = client_admin.patch(f"/ai/product-generation/jobs/{job_id}", json={"status": "nope"})
    assert bad.status_code == 400


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
