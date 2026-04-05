"""
Эндпоинты ежедневной AI-сводки.

GET  /dashboard/daily-brief        — получить текущую сводку (за вчера)
POST /dashboard/daily-brief/generate — инициировать генерацию вручную
"""
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.core.feature_flags import is_daily_brief_enabled
from app.models.user import User
from app.models.daily_brief import DailyBrief
from app.schemas.daily_brief import DailyBriefResponse, DailyBriefTriggerResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["daily-brief"])


def _yesterday() -> date:
    return date.today() - timedelta(days=1)


@router.get("/daily-brief", response_model=DailyBriefResponse)
def get_daily_brief(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DailyBriefResponse:
    """
    Вернуть текущую сводку за вчера из кэша.
    Если сводки нет — вернуть status='pending' без запуска генерации.
    Фронт сам решает, когда вызвать /generate.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    yesterday = _yesterday()
    brief = (
        db.query(DailyBrief)
        .filter(
            DailyBrief.user_id == str(current_user.id),
            DailyBrief.date_for == yesterday,
        )
        .first()
    )

    if brief is None:
        return DailyBriefResponse(
            date_for=yesterday.isoformat(),
            status="pending",
            text=None,
            error_message=None,
            generated_at=None,
        )

    return DailyBriefResponse(
        date_for=brief.date_for.isoformat(),
        status=brief.status,
        text=brief.text,
        error_message=brief.error_message,
        generated_at=brief.generated_at,
    )


@router.post("/daily-brief/generate", response_model=DailyBriefTriggerResponse)
def trigger_daily_brief(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DailyBriefTriggerResponse:
    """
    Инициировать генерацию сводки за вчера.
    Если сводка уже готова (status=ready) — вернуть статус без повторной генерации.
    Если идёт генерация — вернуть статус без дублирования задачи.
    """
    if not is_daily_brief_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI-сводка временно отключена",
        )
    yesterday = _yesterday()
    user_id = str(current_user.id)

    brief = (
        db.query(DailyBrief)
        .filter(
            DailyBrief.user_id == user_id,
            DailyBrief.date_for == yesterday,
        )
        .first()
    )

    if brief is not None and brief.status == "ready":
        return DailyBriefTriggerResponse(
            status="ready",
            message="Сводка уже готова",
            date_for=yesterday.isoformat(),
        )

    if brief is not None and brief.status == "generating":
        return DailyBriefTriggerResponse(
            status="generating",
            message="Генерация уже запущена",
            date_for=yesterday.isoformat(),
        )

    # Создать или обновить запись в pending
    if brief is None:
        brief = DailyBrief(
            user_id=user_id,
            date_for=yesterday,
            status="pending",
        )
        db.add(brief)
        db.commit()
        db.refresh(brief)
    else:
        # Был error или pending — сбрасываем для повтора
        brief.status = "pending"
        brief.error_message = None
        brief.text = None
        db.commit()

    # Отправить задачу в Celery
    try:
        from celery_app.tasks import generate_daily_brief as celery_task
        celery_task.delay(user_id, yesterday.isoformat())
    except Exception as exc:
        logger.error("Не удалось поставить задачу generate_daily_brief: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось запустить генерацию сводки",
        )

    return DailyBriefTriggerResponse(
        status="generating",
        message="Генерация запущена",
        date_for=yesterday.isoformat(),
    )
