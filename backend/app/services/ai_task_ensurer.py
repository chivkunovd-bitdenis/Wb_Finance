from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.ai_task import AiTask


def ensure_wb_access_grant_task(*, db: Session, user_id: str, reason: str | None = None) -> AiTask:
    """Expose one open task asking the user to refresh WB browser access."""
    dedupe_key = "task:wb_access_grant"
    existing = _open_task_by_dedupe(db=db, user_id=user_id, dedupe_key=dedupe_key)
    if existing is not None:
        if reason is not None:
            existing.reason = reason
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing

    row = AiTask(
        user_id=user_id,
        nm_id=None,
        task_type="wb_access_grant",
        title="Дать доступ к кабинету WB",
        description="Нужно один раз авторизоваться, чтобы система могла получать отчёт сравнения с конкурентами.",
        reason=reason,
        current_value=None,
        priority=100,
        status="new",
        fingerprint=None,
        dedupe_key=dedupe_key,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_competitor_report_refresh_task(
    *,
    db: Session,
    user_id: str,
    period: str,
    reason: str | None = None,
) -> AiTask:
    """Expose one open task asking the user to manually reopen/confirm WB comparison report."""
    p = period if period in {"week", "month", "quarter"} else "week"
    dedupe_key = f"task:competitor_report_refresh:{p}"
    existing = _open_task_by_dedupe(db=db, user_id=user_id, dedupe_key=dedupe_key)
    current_value = {"period": p}
    if existing is not None:
        existing.current_value = current_value
        existing.title = "Обновить отчёт сравнения с конкурентами"
        existing.description = "Операция может быть платной/лимитной — требуется подтверждение"
        existing.reason = reason or "Сравнение устарело или WB просит переоткрыть отчёт."
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    row = AiTask(
        user_id=user_id,
        nm_id=None,
        task_type="competitor_report_refresh",
        title="Обновить отчёт сравнения с конкурентами",
        description="Операция может быть платной/лимитной — требуется подтверждение",
        reason=reason or "Сравнение устарело или WB просит переоткрыть отчёт.",
        current_value=current_value,
        priority=50,
        status="new",
        fingerprint=None,
        dedupe_key=dedupe_key,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _open_task_by_dedupe(*, db: Session, user_id: str, dedupe_key: str) -> AiTask | None:
    return (
        db.query(AiTask)
        .filter(
            AiTask.user_id == user_id,
            AiTask.dedupe_key == dedupe_key,
            AiTask.status.in_(["new", "in_progress"]),
        )
        .order_by(AiTask.created_at.desc())
        .first()
    )
