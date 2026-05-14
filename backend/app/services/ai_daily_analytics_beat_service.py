from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from datetime import date

from sqlalchemy.orm import Session

from app.models.user import User
from app.services.ai_competitor_service import get_latest_report
from app.services.ai_daily_analytics_service import run_daily_analytics
from app.services.ai_task_ensurer import ensure_competitor_report_refresh_task, ensure_wb_access_grant_task
from app.services.ai_wb_access_service import wb_headless_access_effective, wb_reconnect_required

logger = logging.getLogger(__name__)

_ALLOWED_PERIODS = frozenset({"week", "month", "quarter"})
FetchReport = Callable[[str, str], dict]


def run_ai_daily_analytics_beat_cycle(
    *,
    db: Session,
    today: date,
    period: str,
    limit_user_ids: Collection[str] | None = None,
    fetch_report: FetchReport | None = None,
) -> dict:
    """
    Для каждого активного пользователя поддерживает ежедневный контур WB «Сравнение карточек»:
    - если storage_state нет/протух — показывает задачу на повторный вход;
    - если отчёт истёк по TTL — показывает задачу на ручное продление/переоткрытие;
    - если отчёт доступен — пробует headless-fetch (это также daily keepalive storage_state);
    - manual reports keep old behavior: run analytics without Playwright.

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
    fetched = 0
    access_tasks_created = 0
    refresh_tasks_created = 0
    skipped_wb_access = 0
    failures: list[dict[str, str]] = []

    for (uid,) in user_ids_raw:
        uid_str = str(uid)
        try:
            if not wb_headless_access_effective(user_id=uid_str):
                ensure_wb_access_grant_task(
                    db=db,
                    user_id=uid_str,
                    reason="WB-сессия не найдена или требует повторного входа перед ежедневным считыванием отчёта.",
                )
                access_tasks_created += 1
                skipped_wb_access += 1
                continue

            rep = get_latest_report(db=db, user_id=uid_str, period=p)
            if rep is None:
                out = _fetch_report_for_user(fetch_report=fetch_report, user_id=uid_str, period=p)
                if bool(out.get("ok")):
                    fetched += 1
                    processed += 1
                    continue
                _handle_fetch_error(db=db, user_id=uid_str, period=p, out=out)
                if out.get("error") == "paid_reopen_required":
                    refresh_tasks_created += 1
                elif wb_reconnect_required(user_id=uid_str) or out.get("error") in {"auth_failed", "playwright_error"}:
                    access_tasks_created += 1
                    skipped_wb_access += 1
                else:
                    skipped_no_report += 1
                    failures.append({"user_id": uid_str, "error": str(out.get("error") or "fetch_failed")[:500]})
                continue
            if rep.status != "ready":
                skipped_stale_or_not_ready += 1
                continue
            if rep.valid_until is not None and rep.valid_until < today:
                rep.status = "stale"
                db.add(rep)
                db.commit()
                ensure_competitor_report_refresh_task(
                    db=db,
                    user_id=uid_str,
                    period=p,
                    reason="WB отчёт сравнения доступен только 3 дня; нужно вручную переоткрыть его в кабинете WB.",
                )
                refresh_tasks_created += 1
                skipped_stale_or_not_ready += 1
                continue

            if rep.source != "playwright":
                run_daily_analytics(db=db, user_id=uid_str, report_id=str(rep.id), date_for=today)
                processed += 1
                continue

            out = _fetch_report_for_user(fetch_report=fetch_report, user_id=uid_str, period=p)
            if bool(out.get("ok")):
                fetched += 1
                processed += 1
                continue

            _handle_fetch_error(db=db, user_id=uid_str, period=p, out=out)
            if out.get("error") == "paid_reopen_required":
                refresh_tasks_created += 1
                skipped_stale_or_not_ready += 1
                continue
            if wb_reconnect_required(user_id=uid_str) or out.get("error") in {"auth_failed", "playwright_error"}:
                access_tasks_created += 1
                skipped_wb_access += 1
                continue

            failures.append({"user_id": uid_str, "error": str(out.get("error") or "fetch_failed")[:500]})
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
        "fetched": fetched,
        "access_tasks_created": access_tasks_created,
        "refresh_tasks_created": refresh_tasks_created,
        "skipped_wb_access": skipped_wb_access,
        "skipped_no_report": skipped_no_report,
        "skipped_stale_or_not_ready": skipped_stale_or_not_ready,
        "failures": failures,
    }


def _fetch_report_for_user(*, fetch_report: FetchReport | None, user_id: str, period: str) -> dict:
    if fetch_report is not None:
        return fetch_report(user_id, period)

    from celery_app.tasks import ai_competitor_report_fetch_playwright

    return ai_competitor_report_fetch_playwright(user_id, period)


def _handle_fetch_error(*, db: Session, user_id: str, period: str, out: dict) -> None:
    err = str(out.get("error") or "")
    if err == "paid_reopen_required":
        ensure_competitor_report_refresh_task(
            db=db,
            user_id=user_id,
            period=period,
            reason="WB просит вручную переоткрыть/продлить отчёт сравнения карточек перед новым считыванием.",
        )
        return
    if err in {"auth_failed", "playwright_error"} or wb_reconnect_required(user_id=user_id):
        ensure_wb_access_grant_task(
            db=db,
            user_id=user_id,
            reason="WB-сессия протухла: нужно заново открыть отдельный браузер и войти по SMS-коду.",
        )
