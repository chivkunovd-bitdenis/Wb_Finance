from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.dependencies import get_current_user
from app.db import get_db
from app.models.user import User
from app.schemas.offer_ai import (
    OfferAskRequest,
    OfferAskResponse,
    OfferChatAskRequest,
    OfferChatAskResponse,
    OfferChatHistoryItem,
    OfferChatHistoryResponse,
    OfferChatStartRequest,
    OfferChatStartResponse,
    OfferSourceItem,
    OfferStatusResponse,
    OfferUploadResponse,
)
from app.services.offer_index_state import get_offer_index_state, mark_indexing
from app.services.offer_chat_service import chat_ask, get_or_create_chat, load_history, require_admin
from app.services.offer_rag_service import compute_offer_version, ask_offer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/offer", tags=["offer-ai"])


def _require_admin(current_user: User) -> None:
    try:
        require_admin(current_user)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _safe_meta(obj) -> dict | None:
    m = getattr(obj, "metadata", None)
    return m if isinstance(m, dict) else None


def _offer_dir() -> Path:
    """
    Каталог для загруженной оферты.

    В docker compose он монтируется как /app/data (см. volumes), поэтому дефолт = /app/data/offers.
    Для локальных тестов/запусков можно переопределить OFFER_DATA_DIR.
    """
    base = (os.getenv("OFFER_DATA_DIR") or "/app/data/offers").strip()
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/status", response_model=OfferStatusResponse)
def offer_status(current_user: User = Depends(get_current_user)) -> OfferStatusResponse:
    st = get_offer_index_state()
    return OfferStatusResponse(
        status=st.status,
        active_version=st.active_version,
        indexed_at=st.indexed_at,
        error_message=st.error_message,
    )


@router.post("/upload", response_model=OfferUploadResponse)
def upload_offer(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> OfferUploadResponse:
    name = (file.filename or "offer").lower()
    if not (name.endswith(".pdf") or name.endswith(".txt") or name.endswith(".html")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Поддерживаются файлы .pdf, .txt, .html",
        )
    raw = file.file.read()
    if not raw or len(raw) < 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл пустой или слишком маленький",
        )

    version = compute_offer_version(raw)
    suffix = ".pdf" if name.endswith(".pdf") else (".html" if name.endswith(".html") else ".txt")
    path = _offer_dir() / f"offer_{version}{suffix}"
    path.write_bytes(raw)

    # Поставить задачу индексации (Celery)
    mark_indexing(next_version=version)
    try:
        from celery_app.tasks import index_offer_document as task

        task.delay(str(path), version)
    except Exception as exc:
        logger.exception("offer_ai: failed to enqueue indexing: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Очередь задач недоступна (celery/redis). Индексация не запущена.",
        ) from exc

    return OfferUploadResponse(status="indexing", next_version=version)


@router.post("/ask", response_model=OfferAskResponse)
def offer_ask(
    body: OfferAskRequest,
    current_user: User = Depends(get_current_user),
) -> OfferAskResponse:
    st = get_offer_index_state()
    if st.status != "ready" or not st.active_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Оферта ещё не проиндексирована. Сначала загрузите файл и дождитесь статуса ready.",
        )

    try:
        answer, sources = ask_offer(question=body.question, active_version=st.active_version)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("offer_ai: ask failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось получить ответ") from exc

    return OfferAskResponse(
        answer=answer,
        sources=[
            OfferSourceItem(
                chunk_id=s.chunk_id,
                score=s.score,
                text=s.text,
                metadata=_safe_meta(s),
            )
            for s in sources[:6]
        ],
        active_version=st.active_version,
    )


@router.post("/chat/start", response_model=OfferChatStartResponse)
def offer_chat_start(
    body: OfferChatStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OfferChatStartResponse:
    _require_admin(current_user)
    st = get_offer_index_state()
    if st.status != "ready" or not st.active_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Оферта ещё не проиндексирована. Сначала загрузите файл и дождитесь статуса ready.",
        )
    chat_id = str(body.chat_id)
    get_or_create_chat(db=db, chat_id=chat_id, user=current_user, offer_version=st.active_version)
    return OfferChatStartResponse(chat_id=body.chat_id, active_version=st.active_version)


@router.post("/chat/reset")
def offer_chat_reset(
    body: OfferChatStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(current_user)
    from app.services.offer_chat_service import reset_chat

    reset_chat(db=db, chat_id=str(body.chat_id), user=current_user)
    return {"status": "ok"}


@router.get("/chat/history", response_model=OfferChatHistoryResponse)
def offer_chat_history(
    chat_id: str = Query(min_length=8, max_length=64),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OfferChatHistoryResponse:
    _require_admin(current_user)
    st = get_offer_index_state()
    if st.status != "ready" or not st.active_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Оферта ещё не проиндексирована. Сначала загрузите файл и дождитесь статуса ready.",
        )
    msgs = load_history(db=db, chat_id=chat_id.strip(), user=current_user, offer_version=st.active_version)
    return OfferChatHistoryResponse(
        chat_id=chat_id.strip(),
        active_version=st.active_version,
        messages=[
            OfferChatHistoryItem(
                role=m.role,
                content=m.content,
                created_at=m.created_at.isoformat() if m.created_at else "",
            )
            for m in msgs
        ],
    )


@router.post("/chat/ask", response_model=OfferChatAskResponse)
def offer_chat_ask(
    body: OfferChatAskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OfferChatAskResponse:
    _require_admin(current_user)
    st = get_offer_index_state()
    if st.status != "ready" or not st.active_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Оферта ещё не проиндексирована. Сначала загрузите файл и дождитесь статуса ready.",
        )
    chat_id = str(body.chat_id)
    # Ensure chat exists & belongs to user
    get_or_create_chat(db=db, chat_id=chat_id, user=current_user, offer_version=st.active_version)

    try:
        res = chat_ask(
            db=db,
            user=current_user,
            chat_id=chat_id,
            offer_version=st.active_version,
            message=body.message,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("offer_ai: chat ask failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось получить ответ") from exc

    sources = res.get("sources") or []
    return OfferChatAskResponse(
        chat_id=body.chat_id,
        answer=str(res.get("answer") or ""),
        sources=[
            OfferSourceItem(
                chunk_id=s.chunk_id,
                score=s.score,
                text=s.text,
                metadata=_safe_meta(s),
            )
            for s in sources[:6]
        ],
        active_version=st.active_version,
        need_clarification=bool(res.get("need_clarification")),
    )

