from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import date

from sqlalchemy.orm import Session

from app.models.user import User
from app.services.ai_competitor_service import get_latest_report
from app.services.ai_daily_analytics_service import run_daily_analytics

logger = logging.getLogger(__name__)

_ALLOWED_PERIODS = frozenset({"week", "month", "quarter"})


def run_ai_daily_analytics_beat_cycle(
    *,
    db: Session,
    today: date,
    period: str,
    limit_user_ids: Collection[str] | None = None,
) -> dict:
    """
    Для каждого активного пользователя: если есть актуальный конкурентный отчёт
    (status=ready, valid_until>=today по выбранному period) — запускает `run_daily_analytics`.

    При ошибке у одного пользователя остальные всё равно обрабатываются.
    Контракт возврата: `ok` False, если хотя бы один пользователь завершился с ошибкой.

    limit_user_ids: опционально (тесты/staging): только эти UUID-строки.
    """
    p = (period or "week").strip().lower()
    if p not in _ALLOWED_PERIODS:
        p = "week"

    query = db.query(User.id).filter(User.is_active.is_(True))
    if limit_user_ids is not None:
        ids_raw = frozenset(str(x) for x in limit_user_ids)
        query = query.filter(User.id.in_(ids_raw))

    user_ids_raw = query.all()

    skipped_no_report = 0
    skipped_stale_or_not_ready = 0
    processed = 0
    failures: list[dict[str, str]] = []

    for (uid,) in user_ids_raw:
        uid_str = str(uid)
        try:
            rep = get_latest_report(db=db, user_id=uid_str, period=p)
            if rep is None:
                skipped_no_report += 1
                continue
            if rep.status != "ready":
                skipped_stale_or_not_ready += 1
                continue
            if rep.valid_until is not None and rep.valid_until < today:
                skipped_stale_or_not_ready += 1
                continue

            run_daily_analytics(db=db, user_id=uid_str, report_id=str(rep.id), date_for=today)
            processed += 1
        except Exception as exc:
            logger.exception("ai_daily_analytics_beat: failed for user %s", uid_str)
            failures.append({"user_id": uid_str, "error": str(exc)[:500]})

    ok = len(failures) == 0
    return {
        "ok": ok,
        "today": today.isoformat(),
        "period": p,
        "users_considered": len(user_ids_raw),
        "processed": processed,
        "skipped_no_report": skipped_no_report,
        "skipped_stale_or_not_ready": skipped_stale_or_not_ready,
        "failures": failures,
    }
