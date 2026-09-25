"""
Агентский цикл единого ИИ-чата (/dashboard/assistant/ask).

Используем OpenAI Responses API (POST {AI_API_BASE_URL}/responses через httpx — не SDK,
чтобы не тащить лишнюю зависимость и остаться на тех же env-переменных, что и остальной
проект: AI_API_KEY, AI_API_BASE_URL). Модель берётся из AI_CHAT_MODEL (по умолчанию
"gpt-4.1").

Инструменты агента:
  - {"type": "web_search"} — встроенный веб-поиск (свежие правила WB, тарифы, рынок).
  - search_offer(query) — RAG по канонической оферте WB (Qdrant, см. canonical_offer.py
    + offer_rag_service.retrieve_offer_chunks).
  - get_store_data(date_from, date_to) — компактный P&L/воронка/SKU снэпшот активного
    магазина (assistant_store_data_service.get_store_data).

Цикл: system + последние ~20 сообщений истории + новое сообщение → вызов API → если модель
запросила function call(ы) — выполняем и отдаём результат обратно как function_call_output,
повторяем (максимум MAX_ITERATIONS раз) → как только модель вернула финальный текст без
новых tool calls, извлекаем текст + url_citation annotations как источники.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services import assistant_store_data_service
from app.services.canonical_offer import ensure_canonical_offer_indexed
from app.services.daily_brief_service import _build_prompt as _cfo_build_prompt
from app.services.daily_brief_service import build_daily_brief_payload, call_ai
from app.services.offer_rag_service import retrieve_offer_chunks
from app.services.store_access_service import StoreContext

logger = logging.getLogger(__name__)

_AI_API_BASE = (os.getenv("AI_API_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
_AI_API_KEY = os.getenv("AI_API_KEY") or ""
_AI_CHAT_MODEL = os.getenv("AI_CHAT_MODEL") or "gpt-4.1"
_HTTP_TIMEOUT_SEC = 90.0
_MAX_ITERATIONS = 5
_OFFER_TOP_K = 5
_SNIPPET_CHARS = 400


def _require_ai_key() -> None:
    if not _AI_API_KEY:
        raise ValueError("AI_API_KEY не задан (нужен для работы ИИ-чата).")


def _tools_spec() -> list[dict[str, Any]]:
    return [
        {"type": "web_search"},
        {
            "type": "function",
            "name": "search_offer",
            "description": (
                "Поиск релевантных фрагментов в канонической публичной оферте Wildberries "
                "(векторный поиск). Используй для любых вопросов про условия, правила, "
                "штрафы, недовложение, комиссии, ответственность сторон и т.п. по оферте WB."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос на русском по тексту оферты",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_store_data",
            "description": (
                "Получить компактные данные продавца за период: дневной P&L (выручка, "
                "комиссия, логистика, хранение, штрафы, реклама, себестоимость, налог, "
                "маржа), итоги за период, топ/антитоп SKU по марже и выручке, итоги воронки "
                "(показы, корзина, заказы). Период капается 60 днями."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "YYYY-MM-DD, включительно"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD, включительно"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


def _system_prompt(db: Session, store_owner_id: str) -> str:
    date_hint = assistant_store_data_service.today_and_data_max_date_hint(db, store_owner_id=store_owner_id)
    return f"""Ты AI CFO и ассистент продавца Wildberries. Отвечай по-русски, кратко и по делу,
суммы указывай в рублях со знаком ₽. Если нужных данных нет — прямо скажи об этом, не выдумывай
цифры и не додумывай значения.

{date_hint}

Инструменты:
- get_store_data(date_from, date_to) — используй для вопросов про выручку, прибыль, маржу,
  рекламу, конкретные SKU, динамику. Если период в вопросе не указан явно, используй разумный
  дефолт (последние 7 или 30 дней от даты максимума данных) и явно скажи, за какой период отвечаешь.
- search_offer(query) — используй для вопросов про правила/условия/оферту WB. В ответе кратко
  процитируй релевантный пункт оферты своими словами, не выдумывай формулировки.
- web_search — используй только когда нужна свежая внешняя информация (новости WB, актуальные
  тарифы, изменения правил, рыночные данные), которой нет во внутренних данных и в оферте. Если
  использовал web_search — обязательно укажи источники (ссылки) в ответе.

Никогда не изобретай цифры, которых нет в данных инструментов. Если вопрос касается и данных
магазина, и оферты — вызови оба инструмента."""


def _extract_text_and_sources(output_items: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    for item in output_items:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") not in ("output_text", "text"):
                continue
            t = str(content.get("text") or "")
            if t:
                text_parts.append(t)
            for ann in content.get("annotations") or []:
                if ann.get("type") == "url_citation":
                    sources.append(
                        {
                            "type": "web",
                            "url": ann.get("url"),
                            "title": ann.get("title") or ann.get("url"),
                        }
                    )
    return "\n".join(text_parts).strip(), sources


def _call_responses_api(*, input_items: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    _require_ai_key()
    url = f"{_AI_API_BASE}/responses"
    headers = {"Authorization": f"Bearer {_AI_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": _AI_CHAT_MODEL,
        "input": input_items,
        "tools": tools,
        "tool_choice": "auto",
    }
    resp = httpx.post(url, headers=headers, json=body, timeout=_HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.json()


def _run_search_offer(*, query: str) -> tuple[str, list[dict[str, Any]]]:
    version = ensure_canonical_offer_indexed()
    chunks = retrieve_offer_chunks(query=query, version=version, top_k=_OFFER_TOP_K)
    sources = [
        {
            "type": "offer",
            "chunk_id": c["chunk_id"],
            "text": c["text"][:_SNIPPET_CHARS],
        }
        for c in chunks
    ]
    return json.dumps(chunks, ensure_ascii=False), sources


def _run_get_store_data(*, db: Session, store_owner_id: str, args: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    data = assistant_store_data_service.get_store_data(
        db,
        store_owner_id=store_owner_id,
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
    )
    return json.dumps(data, ensure_ascii=False), []


def _execute_tool(
    *, db: Session, store_owner_id: str, name: str, arguments: str | None
) -> tuple[str, list[dict[str, Any]]]:
    try:
        args = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError):
        args = {}

    if name == "search_offer":
        query = str(args.get("query") or "").strip()
        if not query:
            return json.dumps({"error": "empty query"}), []
        try:
            return _run_search_offer(query=query)
        except Exception as exc:  # noqa: BLE001
            logger.exception("assistant_agent: search_offer failed: %s", exc)
            return json.dumps({"error": str(exc)}), []

    if name == "get_store_data":
        try:
            return _run_get_store_data(db=db, store_owner_id=store_owner_id, args=args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("assistant_agent: get_store_data failed: %s", exc)
            return json.dumps({"error": str(exc)}), []

    return json.dumps({"error": f"unknown tool {name}"}), []


@dataclass(frozen=True)
class AgentResult:
    content: str
    sources: list[dict[str, Any]]


def run_agent(
    db: Session,
    *,
    store_ctx: StoreContext,
    history: list[Any],
    user_message: str,
) -> AgentResult:
    """
    history: список объектов с полями .role / .content (OfferAiMessage), уже в хронологическом
    порядке (старые → новые), ограниченный вызывающей стороной (см. AGENT_CONTEXT_LIMIT).
    """
    store_owner_id = str(store_ctx.store_owner.id)
    system_prompt = _system_prompt(db, store_owner_id)

    input_items: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in history:
        role = "user" if m.role == "user" else "assistant"
        input_items.append({"role": role, "content": m.content})
    input_items.append({"role": "user", "content": user_message})

    tools = _tools_spec()
    collected_sources: list[dict[str, Any]] = []
    final_text = ""

    for _ in range(_MAX_ITERATIONS):
        resp = _call_responses_api(input_items=input_items, tools=tools)
        output_items = list(resp.get("output") or [])
        input_items = input_items + output_items

        function_calls = [it for it in output_items if it.get("type") == "function_call"]
        if not function_calls:
            final_text, citation_sources = _extract_text_and_sources(output_items)
            collected_sources.extend(citation_sources)
            break

        for fc in function_calls:
            name = str(fc.get("name") or "")
            call_id = fc.get("call_id") or fc.get("id") or ""
            output_str, fn_sources = _execute_tool(
                db=db,
                store_owner_id=store_owner_id,
                name=name,
                arguments=fc.get("arguments"),
            )
            collected_sources.extend(fn_sources)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_str,
                }
            )
    else:
        logger.warning("assistant_agent: max iterations reached without final text")

    if not final_text:
        final_text = "Не удалось получить ответ модели за отведённое число шагов. Попробуйте переформулировать вопрос."

    return AgentResult(content=final_text, sources=collected_sources)


def run_cfo_analysis(db: Session, *, store_owner_id: str, date_for: date | None) -> str:
    """
    Тот же payload/prompt, что и у ежедневной AI CFO сводки (daily_brief_service), но вызывается
    напрямую по явному действию пользователя — независимо от DAILY_BRIEF_ENABLED (тот флаг
    гейтит только автоматический celery beat, не ручной запуск из чата).
    """
    payload = build_daily_brief_payload(db, store_owner_id, date_for)
    if not payload.launch_skus and not payload.established_skus and not payload.portfolio:
        return "Нет данных за вчера для формирования анализа AI CFO."
    prompt = _cfo_build_prompt(payload)
    return call_ai(prompt)
