from __future__ import annotations

from datetime import date
from functools import lru_cache
from unittest.mock import MagicMock, patch
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.dependencies import get_current_user
from app.main import app
from app.models.ai_review_reply import AiReviewReply
from app.models.user import User


@lru_cache(maxsize=1)
def _ensure_schema() -> None:
    # Reuse the same additive schema sync used by other AI module API tests.
    from tests.test_ai_module_api import _ensure_ai_module_schema  # type: ignore

    _ensure_ai_module_schema()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    _ensure_schema()
    user_id = "00000000-0000-0000-0000-000000000111"
    app.dependency_overrides[get_current_user] = lambda: MagicMock(  # type: ignore[assignment]
        id=user_id,
        is_active=True,
        is_admin=True,
    )
    from app.dependencies import get_store_context  # local import to avoid cycles

    app.dependency_overrides[get_store_context] = lambda: MagicMock(  # type: ignore[assignment]
        store_owner=MagicMock(id=user_id),
    )

    # Ensure user exists.
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.id == user_id).first()
        if not existing:
            db.add(
                User(
                    id=user_id,
                    email="ai-reviews@example.com",
                    password_hash="x",
                    wb_api_key=None,
                    is_admin=True,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_store_context, None)


def test_review_replies_sync_requires_wb_key(client: TestClient) -> None:
    # Ensure WB key is empty even if other tests modified the shared dev DB.
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(id="00000000-0000-0000-0000-000000000111").first()
        assert u is not None
        u.wb_api_key = None
        db.add(u)
        db.commit()
    finally:
        db.close()
    r = client.post("/ai/review-replies/sync")
    assert r.status_code == 400


def test_review_replies_pending_empty(client: TestClient) -> None:
    r = client.get("/ai/review-replies/pending")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data.get("items"), list)


def test_review_reply_publish_marks_published(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    feedback_id = f"fb-{__import__('uuid').uuid4()}"

    # Seed a pending reply row directly in DB.
    db = SessionLocal()
    try:
        row = AiReviewReply(
            user_id=user_id,
            feedback_id=feedback_id,
            product_name="Test",
            author="Anon",
            rating="5",
            review_text="ok",
            suggested_reply="thanks",
            edited_reply=None,
            status="pending",
            last_error=None,
            first_seen_date=date.today(),
            published_at=None,
        )
        db.add(row)
        # ensure WB key present
        u = db.query(User).filter_by(id=user_id).first()
        assert u is not None
        u.wb_api_key = "test-key"
        db.add(u)
        db.commit()
    finally:
        db.close()

    with patch("app.services.ai_review_replies_service.httpx.patch") as mock_patch:
        mock_patch.return_value.status_code = 200
        mock_patch.return_value.text = "{}"
        r = client.post(f"/ai/review-replies/{feedback_id}/publish", json={"text": "edited"})
        assert r.status_code == 200

    # Verify DB row updated.
    db = SessionLocal()
    try:
        row2 = db.query(AiReviewReply).filter_by(user_id=user_id, feedback_id=feedback_id).first()
        assert row2 is not None
        assert row2.status == "published"
        assert row2.edited_reply == "edited"
        assert row2.published_at is not None
    finally:
        db.close()

