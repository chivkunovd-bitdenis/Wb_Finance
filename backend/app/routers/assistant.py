"""
Единый ИИ-чат продавца: /dashboard/assistant/*.

Обычная авторизация + активный магазин (StoreContext), как у остальных /dashboard
эндпоинтов — НЕ ограничено admin/allowlist ИИ-модуля. История чата принадлежит
паре (смотрящий пользователь, владелец магазина): если смотришь чужой магазин по
доступу — увидишь чат для этого магазина, свой собственный чат останется отдельным.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_store_context
from app.schemas.assistant import (
    AssistantAskRequest,
    AssistantCfoAnalysisRequest,
    AssistantHistoryResponse,
    AssistantMessageItem,
)
from app.services.assistant_agent_service import run_agent
from app.services.assistant_chat_service import (
    AGENT_CONTEXT_LIMIT,
    HISTORY_LIMIT,
    append_message,
    get_or_create_assistant_chat,
    load_history,
)
from app.services.cfo_audit_service import resolve_default_date_to, run_cfo_audit
from app.services.store_access_service import StoreContext

_CFO_AUDIT_MAX_SPAN_DAYS = 92

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/assistant", tags=["assistant"])


def _to_item(m) -> AssistantMessageItem:
    return AssistantMessageItem(
        id=m.id,
        role=m.role,
        content=m.content,
        created_at=m.created_at.isoformat() if m.created_at else "",
        kind=m.kind or "chat",
        sources=m.retrieved_chunks,
    )


@router.get("/history", response_model=AssistantHistoryResponse)
def assistant_history(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AssistantHistoryResponse:
    chat = get_or_create_assistant_chat(
        db=db, viewer_id=str(store_ctx.viewer.id), store_owner_id=str(store_ctx.store_owner.id)
    )
    msgs = load_history(db=db, chat=chat, limit=HISTORY_LIMIT)
    return AssistantHistoryResponse(messages=[_to_item(m) for m in msgs])


@router.post("/ask", response_model=AssistantMessageItem)
def assistant_ask(
    body: AssistantAskRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AssistantMessageItem:
    chat = get_or_create_assistant_chat(
        db=db, viewer_id=str(store_ctx.viewer.id), store_owner_id=str(store_ctx.store_owner.id)
    )
    history = load_history(db=db, chat=chat, limit=AGENT_CONTEXT_LIMIT)
    append_message(db=db, chat=chat, role="user", content=body.message, kind="chat")

    try:
        result = run_agent(db, store_ctx=store_ctx, history=history, user_message=body.message)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.exception("assistant: ask failed (http): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Не удалось получить ответ от модели"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("assistant: ask failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось получить ответ"
        ) from exc

    msg = append_message(
        db=db, chat=chat, role="assistant", content=result.content, kind="chat", sources=result.sources or None
    )
    return _to_item(msg)


def _parse_iso_date(raw: str, *, field: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неверный формат даты в поле {field} (ожидается YYYY-MM-DD)",
        ) from exc


def _resolve_cfo_range(body: AssistantCfoAnalysisRequest, db: Session, store_owner_id: str) -> tuple[date, date]:
    if body.date_from or body.date_to:
        date_to = _parse_iso_date(body.date_to, field="date_to") if body.date_to else None
        date_from = _parse_iso_date(body.date_from, field="date_from") if body.date_from else None
        if date_to is None:
            date_to = resolve_default_date_to(db, store_owner_id=store_owner_id)
        if date_from is None:
            date_from = date_to - timedelta(days=29)
    elif body.date:
        # Обратная совместимость со старым контрактом {date}: трактуем как date_to,
        # окно — 30 дней назад (как раньше делал daily_brief_service).
        date_to = _parse_iso_date(body.date, field="date")
        date_from = date_to - timedelta(days=29)
    else:
        date_to = resolve_default_date_to(db, store_owner_id=store_owner_id)
        date_from = date_to - timedelta(days=29)

    if date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="date_from не может быть позже date_to"
        )
    span_days = (date_to - date_from).days
    if span_days > _CFO_AUDIT_MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Период не может превышать {_CFO_AUDIT_MAX_SPAN_DAYS} дней",
        )
    return date_from, date_to


@router.post("/cfo-analysis", response_model=AssistantMessageItem)
def assistant_cfo_analysis(
    body: AssistantCfoAnalysisRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AssistantMessageItem:
    chat = get_or_create_assistant_chat(
        db=db, viewer_id=str(store_ctx.viewer.id), store_owner_id=str(store_ctx.store_owner.id)
    )

    store_owner_id = str(store_ctx.store_owner.id)
    date_from, date_to = _resolve_cfo_range(body, db, store_owner_id)

    try:
        text = run_cfo_audit(db, store_owner_id=store_owner_id, date_from=date_from, date_to=date_to)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.exception("assistant: cfo-analysis failed (http): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Не удалось получить анализ AI CFO"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("assistant: cfo-analysis failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось получить анализ AI CFO"
        ) from exc

    period_label = f"{date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"
    content = f"**Анализ AI CFO · {period_label}**\n\n{text}"
    msg = append_message(db=db, chat=chat, role="assistant", content=content, kind="cfo")
    return _to_item(msg)
