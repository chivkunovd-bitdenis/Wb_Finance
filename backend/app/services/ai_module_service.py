from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.models.ai_hypothesis import AiHypothesis
from app.models.ai_hypothesis_daily_log import AiHypothesisDailyLog
from app.models.ai_task import AiTask


@dataclass(frozen=True)
class NotFoundError(Exception):
    message: str


@dataclass(frozen=True)
class InvalidTransitionError(Exception):
    message: str


_TASK_STATUSES = {"new", "in_progress", "completed", "cancelled"}
_HYP_STATUSES = {"draft", "running", "finished", "cancelled"}


def list_tasks(*, db: Session, user_id: str) -> list[AiTask]:
    return (
        db.query(AiTask)
        .filter(AiTask.user_id == user_id)
        .order_by(AiTask.created_at.desc())
        .all()
    )


def get_task(*, db: Session, user_id: str, task_id: str) -> AiTask:
    row = db.query(AiTask).filter(AiTask.id == task_id, AiTask.user_id == user_id).first()
    if not row:
        raise NotFoundError("Task not found")
    return row


def update_task_status(*, db: Session, user_id: str, task_id: str, status: str) -> AiTask:
    status = (status or "").strip()
    if status not in _TASK_STATUSES:
        raise InvalidTransitionError("Invalid task status")

    row = get_task(db=db, user_id=user_id, task_id=task_id)
    prev = row.status
    if prev == status:
        return row

    if row.task_type == "wb_access_grant" and status == "completed":
        # This "task" is a UX wrapper around "access is saved" (storage_state exists).
        # Do not allow completing it manually before access is actually granted,
        # otherwise the list will immediately re-create it.
        from app.services.ai_wb_access_service import user_storage_state_path

        p = user_storage_state_path(user_id=user_id)
        has_access = p.is_file() and p.stat().st_size >= 50
        if not has_access:
            raise InvalidTransitionError("WB access is not granted yet — нажмите «Выдать доступ» и сохраните сессию")

    # Minimal, predictable transitions for MVP-1
    allowed: set[tuple[str, str]] = {
        ("new", "completed"),
        ("new", "in_progress"),
        ("new", "cancelled"),
        ("in_progress", "completed"),
        ("in_progress", "cancelled"),
    }
    if (prev, status) not in allowed:
        raise InvalidTransitionError(f"Task transition not allowed: {prev} -> {status}")

    now = datetime.now(UTC)
    row.status = status
    if status == "in_progress" and row.started_at is None:
        row.started_at = now
    if status == "completed":
        if row.started_at is None:
            row.started_at = now
        row.completed_at = now
    if status == "cancelled":
        # do not force completed_at
        pass

    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def execute_task(*, db: Session, user_id: str, task_id: str) -> None:
    """
    Execute a task that represents an explicit user confirmation (spend/automation).

    Contract:
    - must be idempotent (re-executing should not enqueue multiple identical jobs)
    - only allowed for specific task_type values
    """
    row = get_task(db=db, user_id=user_id, task_id=task_id)
    if row.status not in {"new", "in_progress"}:
        raise InvalidTransitionError("Task can be executed only when open")

    if row.task_type not in {"competitor_report_refresh", "competitor_report_create"}:
        raise InvalidTransitionError("Task type is not executable")

    # If already in_progress, treat as idempotent no-op.
    if row.status == "in_progress":
        return

    # Mark as in_progress (allowed transition new -> in_progress)
    update_task_status(db=db, user_id=user_id, task_id=task_id, status="in_progress")



def list_hypotheses(*, db: Session, user_id: str) -> list[AiHypothesis]:
    return (
        db.query(AiHypothesis)
        .filter(AiHypothesis.user_id == user_id)
        .order_by(AiHypothesis.created_at.desc())
        .all()
    )


def get_hypothesis(*, db: Session, user_id: str, hypothesis_id: str) -> AiHypothesis:
    row = (
        db.query(AiHypothesis)
        .filter(AiHypothesis.id == hypothesis_id, AiHypothesis.user_id == user_id)
        .first()
    )
    if not row:
        raise NotFoundError("Hypothesis not found")
    return row


def start_hypothesis(*, db: Session, user_id: str, hypothesis_id: str) -> AiHypothesis:
    row = get_hypothesis(db=db, user_id=user_id, hypothesis_id=hypothesis_id)
    if row.status not in _HYP_STATUSES:
        raise InvalidTransitionError("Invalid hypothesis status")
    if row.status != "draft":
        raise InvalidTransitionError("Hypothesis can be started only from draft")
    now = datetime.now(UTC)
    row.status = "running"
    row.started_at = now
    row.ended_at = None
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def finish_hypothesis(
    *,
    db: Session,
    user_id: str,
    hypothesis_id: str,
    result_summary: str | None,
) -> AiHypothesis:
    row = get_hypothesis(db=db, user_id=user_id, hypothesis_id=hypothesis_id)
    if row.status != "running":
        raise InvalidTransitionError("Hypothesis can be finished only from running")
    now = datetime.now(UTC)
    row.status = "finished"
    row.ended_at = now
    if result_summary is not None:
        row.result_summary = result_summary
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_hypothesis_daily_logs(*, db: Session, user_id: str, hypothesis_id: str) -> list[AiHypothesisDailyLog]:
    """Все строки дневного лога из `ai_hypothesis_daily_log` (владелец проверяется)."""
    _ = get_hypothesis(db=db, user_id=user_id, hypothesis_id=hypothesis_id)
    return (
        db.query(AiHypothesisDailyLog)
        .filter(AiHypothesisDailyLog.hypothesis_id == hypothesis_id)
        .order_by(AiHypothesisDailyLog.day.asc())
        .all()
    )


def upsert_hypothesis_daily_log(
    *,
    db: Session,
    user_id: str,
    hypothesis_id: str,
    day: date,
    happened: str | None,
    changed: str | None,
    unchanged: str | None,
) -> list[AiHypothesisDailyLog]:
    hyp = get_hypothesis(db=db, user_id=user_id, hypothesis_id=hypothesis_id)
    if hyp.status != "running":
        raise InvalidTransitionError("Daily log is allowed only for running hypotheses")

    row = (
        db.query(AiHypothesisDailyLog)
        .filter(AiHypothesisDailyLog.hypothesis_id == hypothesis_id, AiHypothesisDailyLog.day == day)
        .first()
    )
    if row is None:
        row = AiHypothesisDailyLog(
            hypothesis_id=hypothesis_id,
            day=day,
            happened=happened,
            changed=changed,
            unchanged=unchanged,
        )
        db.add(row)
    else:
        row.happened = happened
        row.changed = changed
        row.unchanged = unchanged
        db.add(row)

    db.commit()

    items = (
        db.query(AiHypothesisDailyLog)
        .filter(AiHypothesisDailyLog.hypothesis_id == hypothesis_id)
        .order_by(AiHypothesisDailyLog.day.asc())
        .all()
    )
    return items

