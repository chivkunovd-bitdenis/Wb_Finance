from __future__ import annotations

from unittest.mock import MagicMock

import app.services.assistant_agent_service as svc
from app.services.store_access_service import StoreContext


class _FakeQuery:
    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def first(self):
        return None


class _FakeDB:
    def query(self, *a, **kw):
        return _FakeQuery()


def _store_ctx() -> StoreContext:
    user = MagicMock()
    user.id = "00000000-0000-0000-0000-000000000002"
    return StoreContext(viewer=user, store_owner=user)


def test_agent_loop_executes_function_call_then_returns_final_text(monkeypatch):
    calls: list[list[dict]] = []
    responses = [
        {
            "output": [
                {"type": "function_call", "call_id": "c1", "name": "get_store_data", "arguments": "{}"},
            ]
        },
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Итоговый ответ по данным магазина", "annotations": []}
                    ],
                }
            ]
        },
    ]

    def fake_call(*, input_items, tools):
        calls.append(input_items)
        return responses[len(calls) - 1]

    monkeypatch.setattr(svc, "_call_responses_api", fake_call)
    monkeypatch.setattr(
        svc.assistant_store_data_service, "get_store_data", lambda db, **kw: {"period_totals": {"margin": 1000}}
    )

    result = svc.run_agent(_FakeDB(), store_ctx=_store_ctx(), history=[], user_message="Как дела с прибылью?")

    assert result.content == "Итоговый ответ по данным магазина"
    assert len(calls) == 2
    # second call must carry the function_call_output fed back for the first call_id
    second_call_input = calls[1]
    outputs = [it for it in second_call_input if it.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "c1"


def test_web_search_tool_is_requested_and_citations_are_surfaced(monkeypatch):
    seen_tools: list[list[dict]] = []

    def fake_call(*, input_items, tools):
        seen_tools.append(tools)
        return {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Актуальный тариф логистики см. по ссылке",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://seller.wildberries.ru/tariffs",
                                    "title": "Тарифы WB",
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(svc, "_call_responses_api", fake_call)

    result = svc.run_agent(
        _FakeDB(), store_ctx=_store_ctx(), history=[], user_message="Какие сейчас тарифы логистики WB?"
    )

    assert {"type": "web_search"} in seen_tools[0]
    assert result.sources == [
        {"type": "web", "url": "https://seller.wildberries.ru/tariffs", "title": "Тарифы WB"}
    ]


def test_search_offer_tool_triggers_canonical_offer_indexing_check(monkeypatch):
    ensure_calls = {"count": 0}

    def fake_ensure():
        ensure_calls["count"] += 1
        return "v-test"

    monkeypatch.setattr(svc, "ensure_canonical_offer_indexed", fake_ensure)
    monkeypatch.setattr(
        svc,
        "retrieve_offer_chunks",
        lambda *, query, version, top_k: [{"chunk_id": 3, "score": 0.8, "text": "Пункт про штрафы за недовложение"}],
    )

    output_str, sources = svc._execute_tool(
        db=_FakeDB(), store_owner_id="u1", name="search_offer", arguments='{"query": "штраф за недовложение"}'
    )

    assert ensure_calls["count"] == 1
    assert sources == [{"type": "offer", "chunk_id": 3, "text": "Пункт про штрафы за недовложение"}]
    assert "штраф" in output_str or "недовложение" in output_str


def test_max_iterations_returns_fallback_text(monkeypatch):
    def fake_call(*, input_items, tools):
        return {"output": [{"type": "function_call", "call_id": "loop", "name": "get_store_data", "arguments": "{}"}]}

    monkeypatch.setattr(svc, "_call_responses_api", fake_call)
    monkeypatch.setattr(svc.assistant_store_data_service, "get_store_data", lambda db, **kw: {})

    result = svc.run_agent(_FakeDB(), store_ctx=_store_ctx(), history=[], user_message="?")

    assert result.content
    assert "Не удалось" in result.content
