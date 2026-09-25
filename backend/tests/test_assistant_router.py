from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import app.routers.assistant as router_mod
from app.models.offer_ai_chat import OfferAiChat
from app.schemas.assistant import AssistantAskRequest, AssistantCfoAnalysisRequest
from app.services.store_access_service import StoreContext


class _FakeChatQuery:
    def __init__(self, chat: OfferAiChat, on_update=None) -> None:
        self._chat = chat
        self._on_update = on_update

    def filter(self, *a, **kw) -> "_FakeChatQuery":
        return self

    def first(self) -> OfferAiChat:
        return self._chat

    def update(self, values: dict) -> int:
        if self._on_update:
            self._on_update(values)
        return 1


class _FakeMessageQuery:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def filter(self, *a, **kw) -> "_FakeMessageQuery":
        return self

    def order_by(self, *a, **kw) -> "_FakeMessageQuery":
        return self

    def limit(self, *a, **kw) -> "_FakeMessageQuery":
        return self

    def all(self) -> list:
        return list(self._rows)


class _FakeSession:
    """Records .add()-ed objects; enough for router-level tests without a real DB."""

    def __init__(self, chat: OfferAiChat, history: list | None = None) -> None:
        self.chat = chat
        self.history = history or []
        self.added: list = []

    def query(self, model):
        if model is OfferAiChat:
            def _on_update(values: dict) -> None:
                for k, v in values.items():
                    setattr(self.chat, k, v)

            return _FakeChatQuery(self.chat, on_update=_on_update)
        from app.models.offer_ai_message import OfferAiMessage

        if model is OfferAiMessage:
            return _FakeMessageQuery(self.history)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        return None

    def refresh(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.datetime.now(datetime.UTC)


def _store_ctx(user_id: str = "00000000-0000-0000-0000-000000000002") -> StoreContext:
    user = MagicMock()
    user.id = user_id
    return StoreContext(viewer=user, store_owner=user)


def _seed_chat(user_id: str) -> OfferAiChat:
    return OfferAiChat(
        id=str(uuid.uuid4()),
        user_id=user_id,
        store_owner_id=user_id,
        kind="assistant",
        offer_version=None,
    )


def test_cfo_analysis_endpoint_stores_message_with_kind_cfo(monkeypatch):
    store_ctx = _store_ctx()
    chat = _seed_chat(str(store_ctx.store_owner.id))
    db = _FakeSession(chat)

    captured = {}

    def fake_run_cfo_audit(db_arg, *, store_owner_id, date_from, date_to):
        captured["store_owner_id"] = store_owner_id
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return "Анализ AI CFO: выручка выросла на 10%"

    monkeypatch.setattr(router_mod, "run_cfo_audit", fake_run_cfo_audit)
    monkeypatch.setattr(
        router_mod, "resolve_default_date_to", lambda db, *, store_owner_id: datetime.date(2026, 9, 24)
    )

    result = router_mod.assistant_cfo_analysis(
        AssistantCfoAnalysisRequest(date=None, date_from=None, date_to=None), store_ctx, db
    )

    assert result.kind == "cfo"
    assert captured["store_owner_id"] == str(store_ctx.store_owner.id)
    # default range: last 30 days ending on the resolved default date_to
    assert captured["date_to"] == datetime.date(2026, 9, 24)
    assert captured["date_from"] == datetime.date(2026, 8, 26)
    # content is prefixed with a period header, then the audit text
    assert result.content.startswith("**Анализ AI CFO · 26.08.2026–24.09.2026**\n\n")
    assert result.content.endswith("Анализ AI CFO: выручка выросла на 10%")
    # the stored ORM object (not just the response) must carry kind="cfo"
    stored_messages = [o for o in db.added if o.__class__.__name__ == "OfferAiMessage"]
    assert len(stored_messages) == 1
    assert stored_messages[0].kind == "cfo"
    assert stored_messages[0].role == "assistant"


def test_cfo_analysis_endpoint_accepts_explicit_date_range(monkeypatch):
    store_ctx = _store_ctx()
    chat = _seed_chat(str(store_ctx.store_owner.id))
    db = _FakeSession(chat)

    captured = {}

    def fake_run_cfo_audit(db_arg, *, store_owner_id, date_from, date_to):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return "ок"

    monkeypatch.setattr(router_mod, "run_cfo_audit", fake_run_cfo_audit)

    result = router_mod.assistant_cfo_analysis(
        AssistantCfoAnalysisRequest(date=None, date_from="2026-09-01", date_to="2026-09-10"), store_ctx, db
    )

    assert captured["date_from"] == datetime.date(2026, 9, 1)
    assert captured["date_to"] == datetime.date(2026, 9, 10)
    assert result.content.startswith("**Анализ AI CFO · 01.09.2026–10.09.2026**")


def test_cfo_analysis_endpoint_rejects_date_from_after_date_to(monkeypatch):
    store_ctx = _store_ctx()
    chat = _seed_chat(str(store_ctx.store_owner.id))
    db = _FakeSession(chat)

    with pytest.raises(HTTPException) as exc_info:
        router_mod.assistant_cfo_analysis(
            AssistantCfoAnalysisRequest(date=None, date_from="2026-09-10", date_to="2026-09-01"), store_ctx, db
        )
    assert exc_info.value.status_code == 400


def test_cfo_analysis_endpoint_rejects_span_over_92_days(monkeypatch):
    store_ctx = _store_ctx()
    chat = _seed_chat(str(store_ctx.store_owner.id))
    db = _FakeSession(chat)

    with pytest.raises(HTTPException) as exc_info:
        router_mod.assistant_cfo_analysis(
            AssistantCfoAnalysisRequest(date=None, date_from="2026-01-01", date_to="2026-12-31"), store_ctx, db
        )
    assert exc_info.value.status_code == 400


def test_cfo_analysis_endpoint_legacy_date_field_uses_30_day_window(monkeypatch):
    store_ctx = _store_ctx()
    chat = _seed_chat(str(store_ctx.store_owner.id))
    db = _FakeSession(chat)

    captured = {}

    def fake_run_cfo_audit(db_arg, *, store_owner_id, date_from, date_to):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return "ок"

    monkeypatch.setattr(router_mod, "run_cfo_audit", fake_run_cfo_audit)

    router_mod.assistant_cfo_analysis(
        AssistantCfoAnalysisRequest(date="2026-09-24", date_from=None, date_to=None), store_ctx, db
    )

    assert captured["date_to"] == datetime.date(2026, 9, 24)
    assert captured["date_from"] == datetime.date(2026, 8, 26)


def test_ask_endpoint_stores_user_and_assistant_messages_with_kind_chat(monkeypatch):
    store_ctx = _store_ctx()
    chat = _seed_chat(str(store_ctx.store_owner.id))
    db = _FakeSession(chat)

    from app.services.assistant_agent_service import AgentResult

    monkeypatch.setattr(
        router_mod,
        "run_agent",
        lambda db_arg, *, store_ctx, history, user_message: AgentResult(content="Ответ ассистента", sources=[]),
    )

    result = router_mod.assistant_ask(AssistantAskRequest(message="Почему упала маржа?"), store_ctx, db)

    assert result.kind == "chat"
    assert result.content == "Ответ ассистента"
    stored = [o for o in db.added if o.__class__.__name__ == "OfferAiMessage"]
    assert [m.role for m in stored] == ["user", "assistant"]
    assert all(m.kind == "chat" for m in stored)
