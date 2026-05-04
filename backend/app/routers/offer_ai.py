from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.offer_ai import (
    OfferAskRequest,
    OfferAskResponse,
    OfferSourceItem,
    OfferStatusResponse,
    OfferUploadResponse,
)
from app.services.offer_index_state import get_offer_index_state, mark_indexing
from app.services.offer_rag_service import compute_offer_version, ask_offer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/offer", tags=["offer-ai"])

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
            OfferSourceItem(chunk_id=s.chunk_id, score=s.score, text=s.text)
            for s in sources[:6]
        ],
        active_version=st.active_version,
    )

