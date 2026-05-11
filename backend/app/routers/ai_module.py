from __future__ import annotations

import logging
import json
import os
import urllib.request
from collections.abc import Sequence

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_store_context
from app.schemas.ai_module import (
    AiCompetitorReportActionItem,
    AiCompetitorReportActionListResponse,
    AiCompetitorReportDetailResponse,
    AiCompetitorReportImportRequest,
    AiCompetitorReportItem,
    AiCompetitorReportListResponse,
    AiCompetitorReportRefreshRequest,
    AiCompetitorReportStatusResponse,
    AiCompetitorMetricItem,
    AiDailyAnalyticsRunRequest,
    AiDailyAnalyticsRunResponse,
    AiHypothesisDailyLogResponse,
    AiHypothesisDailyLogUpsertRequest,
    AiHypothesisDailyLogItem,
    AiHypothesisFinishRequest,
    AiHypothesisFinishResponse,
    AiHypothesisItem,
    AiHypothesisListResponse,
    AiHypothesisStartResponse,
    AiTaskItem,
    AiTaskListResponse,
    AiTaskExecuteResponse,
    AiTaskUpdateRequest,
    AiWbCredentialsStatusResponse,
    AiWbCredentialsUpsertRequest,
)
from app.services.ai_competitor_service import (
    InvalidPayloadError as CompetitorInvalidPayloadError,
    NotFoundError as CompetitorNotFoundError,
    get_report as get_competitor_report,
    get_latest_report as get_latest_competitor_report,
    import_competitor_report,
    list_report_actions,
    list_report_metrics,
    list_reports as list_competitor_reports,
)
from app.services.ai_daily_analytics_service import (
    InvalidPayloadError as AnalyticsInvalidPayloadError,
    NotFoundError as AnalyticsNotFoundError,
    run_daily_analytics,
)
from app.services.ai_module_service import (
    InvalidTransitionError,
    NotFoundError,
    execute_task,
    finish_hypothesis,
    get_hypothesis,
    get_task,
    list_hypotheses,
    list_hypothesis_daily_logs,
    list_tasks,
    start_hypothesis,
    upsert_hypothesis_daily_log,
    update_task_status,
)
from app.services.ai_wb_credentials_service import (
    InvalidPayloadError as CredsInvalidPayloadError,
    credentials_status as get_creds_status,
    upsert_credentials,
)
from app.services.ai_wb_access_service import (
    InteractiveAuthDisabledError,
    InteractiveAuthFailedError,
    interactive_grant_wb_access,
)
from app.services.store_access_service import StoreContext
from celery_app.tasks import ai_competitor_report_fetch_playwright
from app.models.ai_hypothesis_daily_log import AiHypothesisDailyLog
from app.models.ai_task import AiTask

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-module"])


@router.get("/tasks", response_model=AiTaskListResponse)
def ai_tasks_list(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiTaskListResponse:
    user_id = str(store_ctx.store_owner.id)

    # UX: "WB access granted" == per-user Playwright storage_state exists.
    # If access is missing, ensure there's a single open human-readable task.
    from app.services.ai_wb_access_service import user_storage_state_path

    p = user_storage_state_path(user_id=user_id)
    has_access = p.is_file() and p.stat().st_size >= 50
    dedupe_key = "task:wb_access_grant"
    existing = (
        db.query(AiTask)
        .filter(
            AiTask.user_id == user_id,
            AiTask.dedupe_key == dedupe_key,
            AiTask.status.in_(["new", "in_progress"]),
        )
        .order_by(AiTask.created_at.desc())
        .first()
    )
    if has_access:
        if existing is not None:
            # Auto-complete once access is saved; avoids "reappearing" tasks.
            update_task_status(db=db, user_id=user_id, task_id=str(existing.id), status="completed")
    else:
        if existing is None:
            row = AiTask(
                user_id=user_id,
                nm_id=None,
                task_type="wb_access_grant",
                title="Дать доступ к кабинету WB",
                description="Нужно один раз авторизоваться, чтобы система могла получать отчёт сравнения с конкурентами.",
                reason=None,
                current_value=None,
                priority=100,
                status="new",
                fingerprint=None,
                dedupe_key=dedupe_key,
            )
            db.add(row)
            db.commit()

    items = list_tasks(db=db, user_id=user_id)
    return AiTaskListResponse(items=[AiTaskItem.model_validate(x) for x in items])


@router.get("/tasks/{task_id}", response_model=AiTaskItem)
def ai_task_get(
    task_id: str,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiTaskItem:
    try:
        row = get_task(db=db, user_id=str(store_ctx.store_owner.id), task_id=task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return AiTaskItem.model_validate(row)


@router.patch("/tasks/{task_id}", response_model=AiTaskItem)
def ai_task_patch(
    task_id: str,
    body: AiTaskUpdateRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiTaskItem:
    try:
        row = update_task_status(db=db, user_id=str(store_ctx.store_owner.id), task_id=task_id, status=body.status)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiTaskItem.model_validate(row)


@router.post("/tasks/{task_id}/execute", response_model=AiTaskExecuteResponse)
def ai_task_execute(
    task_id: str,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiTaskExecuteResponse:
    user_id = str(store_ctx.store_owner.id)
    try:
        # Will mark task as in_progress if needed
        execute_task(db=db, user_id=user_id, task_id=task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    # Enqueue explicit action job based on task_type
    task = get_task(db=db, user_id=user_id, task_id=task_id)
    if task.task_type in {"competitor_report_refresh", "competitor_report_create"}:
        period = None
        if isinstance(task.current_value, dict):
            period = task.current_value.get("period")
        p = (str(period or "week")).strip().lower()
        try:
            ai_competitor_report_fetch_playwright.delay(user_id, p)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Celery delay failed (ai_competitor_report_fetch_playwright): %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось поставить задачу в очередь (celery/redis недоступны)",
            ) from exc
        return AiTaskExecuteResponse(status="ok", task_id=task_id, message="queued")

    return AiTaskExecuteResponse(status="ok", task_id=task_id, message="noop")


@router.get("/hypotheses", response_model=AiHypothesisListResponse)
def ai_hypotheses_list(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiHypothesisListResponse:
    items = list_hypotheses(db=db, user_id=str(store_ctx.store_owner.id))
    return AiHypothesisListResponse(items=[AiHypothesisItem.model_validate(x) for x in items])


@router.get("/hypotheses/{hypothesis_id}", response_model=AiHypothesisItem)
def ai_hypothesis_get(
    hypothesis_id: str,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiHypothesisItem:
    try:
        row = get_hypothesis(db=db, user_id=str(store_ctx.store_owner.id), hypothesis_id=hypothesis_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return AiHypothesisItem.model_validate(row)


@router.post("/hypotheses/{hypothesis_id}/start", response_model=AiHypothesisStartResponse)
def ai_hypothesis_start(
    hypothesis_id: str,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiHypothesisStartResponse:
    try:
        start_hypothesis(db=db, user_id=str(store_ctx.store_owner.id), hypothesis_id=hypothesis_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiHypothesisStartResponse(status="ok")


@router.post("/hypotheses/{hypothesis_id}/finish", response_model=AiHypothesisFinishResponse)
def ai_hypothesis_finish(
    hypothesis_id: str,
    body: AiHypothesisFinishRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiHypothesisFinishResponse:
    try:
        finish_hypothesis(
            db=db,
            user_id=str(store_ctx.store_owner.id),
            hypothesis_id=hypothesis_id,
            result_summary=body.result_summary,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return AiHypothesisFinishResponse(status="ok")


def _hypothesis_daily_log_response(rows: Sequence[AiHypothesisDailyLog]) -> AiHypothesisDailyLogResponse:
    return AiHypothesisDailyLogResponse(
        items=[
            AiHypothesisDailyLogItem(
                day=x.day,
                happened=x.happened,
                changed=x.changed,
                unchanged=x.unchanged,
                created_at=x.created_at,
                updated_at=x.updated_at,
            )
            for x in rows
        ],
    )


@router.get(
    "/hypotheses/{hypothesis_id}/daily-log",
    response_model=AiHypothesisDailyLogResponse,
)
def ai_hypothesis_daily_log_list(
    hypothesis_id: str,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiHypothesisDailyLogResponse:
    try:
        rows = list_hypothesis_daily_logs(
            db=db,
            user_id=str(store_ctx.store_owner.id),
            hypothesis_id=hypothesis_id,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return _hypothesis_daily_log_response(rows)


@router.post(
    "/hypotheses/{hypothesis_id}/daily-log",
    response_model=AiHypothesisDailyLogResponse,
)
def ai_hypothesis_daily_log_upsert(
    hypothesis_id: str,
    body: AiHypothesisDailyLogUpsertRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiHypothesisDailyLogResponse:
    try:
        items = upsert_hypothesis_daily_log(
            db=db,
            user_id=str(store_ctx.store_owner.id),
            hypothesis_id=hypothesis_id,
            day=body.day,
            happened=body.happened,
            changed=body.changed,
            unchanged=body.unchanged,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return _hypothesis_daily_log_response(items)


@router.post("/competitor-reports/import", response_model=AiCompetitorReportItem)
def ai_competitor_report_import(
    body: AiCompetitorReportImportRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiCompetitorReportItem:
    try:
        row = import_competitor_report(
            db=db,
            user_id=str(store_ctx.store_owner.id),
            report_date=body.report_date,
            period=body.period,
            source=body.source,
            raw_payload=body.raw_payload,
            items=[x.model_dump() for x in body.items],
        )
    except CompetitorInvalidPayloadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return AiCompetitorReportItem.model_validate(row)


@router.put("/wb-credentials", response_model=AiWbCredentialsStatusResponse)
def ai_wb_credentials_upsert(
    body: AiWbCredentialsUpsertRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiWbCredentialsStatusResponse:
    try:
        upsert_credentials(
            db=db,
            user_id=str(store_ctx.store_owner.id),
            wb_login=body.wb_login,
            wb_password=body.wb_password,
        )
    except CredsInvalidPayloadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    st = get_creds_status(db=db, user_id=str(store_ctx.store_owner.id))
    return AiWbCredentialsStatusResponse(**st)


@router.get("/wb-credentials/status", response_model=AiWbCredentialsStatusResponse)
def ai_wb_credentials_status(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiWbCredentialsStatusResponse:
    st = get_creds_status(db=db, user_id=str(store_ctx.store_owner.id))
    return AiWbCredentialsStatusResponse(**st)


@router.post("/wb-access/grant")
def ai_wb_access_grant(
    store_ctx: StoreContext = Depends(get_store_context),
) -> dict:
    """
    Interactive grant flow: opens a browser window (headed Playwright) on the API host,
    lets the user complete WB login, then stores a per-user storage_state snapshot.
    """
    try:
        return interactive_grant_wb_access(user_id=str(store_ctx.store_owner.id))
    except InteractiveAuthDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    except InteractiveAuthFailedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Не удалось выдать доступ: {exc.message}") from exc


@router.post("/wb-access/storage-state")
def ai_wb_access_storage_state_upload(
    file: UploadFile,
    store_ctx: StoreContext = Depends(get_store_context),
) -> dict:
    """
    Upload Playwright storage_state JSON (auth cookies/state) for WB cabinet.

    This is a fallback for environments where interactive headed auth cannot run (e.g. Docker without X server).
    """
    from app.services.ai_wb_access_service import user_storage_state_path

    name = (file.filename or "").lower()
    if not name.endswith(".json"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нужен файл .json")
    raw = file.file.read()  # type: ignore[no-untyped-call]
    if not raw or len(raw) > 2_000_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл пустой или слишком большой")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный JSON") from exc

    if not isinstance(payload, dict) or "cookies" not in payload or "origins" not in payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Это не похоже на файл доступа")

    p = user_storage_state_path(user_id=str(store_ctx.store_owner.id))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return {"status": "ok"}


@router.get("/wb-access/status")
def ai_wb_access_status(
    store_ctx: StoreContext = Depends(get_store_context),
) -> dict:
    """
    Check whether a saved Playwright storage_state snapshot exists for this user.
    This is the real signal of "access is granted" for headless/worker usage.
    """
    from app.services.ai_wb_access_service import user_storage_state_path

    p = user_storage_state_path(user_id=str(store_ctx.store_owner.id))
    ok = p.is_file() and p.stat().st_size >= 50
    return {"status": "ok", "has_storage_state": ok}


@router.post("/wb-access/remote/start")
def ai_wb_access_remote_start(
    store_ctx: StoreContext = Depends(get_store_context),
    payload: dict = Body(default={}),
) -> dict:
    """
    Start remote browser session (noVNC) for WB login on server.
    """
    token = (os.getenv("WB_AUTH_INTERNAL_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WB auth is not configured")
    user_id = str(store_ctx.store_owner.id)
    force = bool(payload.get("force")) if isinstance(payload, dict) else False
    data = json.dumps({"user_id": user_id, "force": force}).encode("utf-8")
    req = urllib.request.Request(
        "http://wb_auth:8081/start",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec - internal network
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"WB auth start failed: {exc}") from exc


@router.post("/wb-access/remote/save")
def ai_wb_access_remote_save(
    store_ctx: StoreContext = Depends(get_store_context),
) -> dict:
    """
    Save storage_state from remote noVNC session.
    """
    token = (os.getenv("WB_AUTH_INTERNAL_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WB auth is not configured")
    user_id = str(store_ctx.store_owner.id)
    data = json.dumps({"user_id": user_id}).encode("utf-8")
    req = urllib.request.Request(
        "http://wb_auth:8081/save",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec - internal network
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"WB auth save failed: {exc}") from exc


@router.post("/wb-access/remote/status")
def ai_wb_access_remote_status(
    store_ctx: StoreContext = Depends(get_store_context),
) -> dict:
    """
    Check whether remote browser session (noVNC) is currently active for user.
    """
    token = (os.getenv("WB_AUTH_INTERNAL_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WB auth is not configured")
    user_id = str(store_ctx.store_owner.id)
    data = json.dumps({"user_id": user_id}).encode("utf-8")
    req = urllib.request.Request(
        "http://wb_auth:8081/status",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec - internal network
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"WB auth status failed: {exc}") from exc


@router.get("/competitor-reports", response_model=AiCompetitorReportListResponse)
def ai_competitor_reports_list(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiCompetitorReportListResponse:
    rows = list_competitor_reports(db=db, user_id=str(store_ctx.store_owner.id))
    return AiCompetitorReportListResponse(items=[AiCompetitorReportItem.model_validate(x) for x in rows])


@router.get("/competitor-reports/status", response_model=AiCompetitorReportStatusResponse)
def ai_competitor_report_status(
    period: str = "week",
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiCompetitorReportStatusResponse:
    p = (period or "week").strip().lower()
    user_id = str(store_ctx.store_owner.id)
    rep = get_latest_competitor_report(db=db, user_id=user_id, period=p)
    if rep is None:
        # UX: if the UI requests "week" but the latest available report is stored
        # under another period (month/quarter), we still consider the report present.
        # This avoids a false "missing" state when the user already has a report in the system.
        from app.models.ai_competitor_report import AiCompetitorComparisonReport

        rep = (
            db.query(AiCompetitorComparisonReport)
            .filter(AiCompetitorComparisonReport.user_id == user_id)
            .order_by(AiCompetitorComparisonReport.report_date.desc(), AiCompetitorComparisonReport.created_at.desc())
            .first()
        )
        if rep is None:
            return AiCompetitorReportStatusResponse(status="missing")
    # Treat expired as stale
    st = rep.status
    if rep.valid_until is not None:
        from datetime import date as date_type

        if rep.valid_until < date_type.today():
            st = "stale"
    return AiCompetitorReportStatusResponse(
        status=st,
        report_id=str(rep.id),
        report_date=rep.report_date,
        valid_until=rep.valid_until,
        last_error=(rep.last_error[:500] if rep.last_error else None),
    )


@router.get("/competitor-reports/actions", response_model=AiCompetitorReportActionListResponse)
def ai_competitor_report_actions_list(
    limit: int = 50,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiCompetitorReportActionListResponse:
    rows = list_report_actions(db=db, user_id=str(store_ctx.store_owner.id), limit=limit)
    return AiCompetitorReportActionListResponse(
        items=[AiCompetitorReportActionItem.model_validate(r) for r in rows],
    )


@router.get("/competitor-reports/{report_id}", response_model=AiCompetitorReportDetailResponse)
def ai_competitor_report_get(
    report_id: str,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiCompetitorReportDetailResponse:
    try:
        rep = get_competitor_report(db=db, user_id=str(store_ctx.store_owner.id), report_id=report_id)
    except CompetitorNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    metrics = list_report_metrics(db=db, report_id=str(rep.id))
    return AiCompetitorReportDetailResponse(
        report=AiCompetitorReportItem.model_validate(rep),
        metrics=[AiCompetitorMetricItem.model_validate(x) for x in metrics],
    )


@router.post("/competitor-reports/request-refresh", response_model=AiTaskItem)
def ai_competitor_report_request_refresh(
    body: AiCompetitorReportRefreshRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiTaskItem:
    period = (body.period or "").strip().lower()
    if period not in {"week", "month", "quarter"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid period")

    user_id = str(store_ctx.store_owner.id)
    dedupe_key = f"task:competitor_report_refresh:{period}"
    existing = (
        db.query(AiTask)
        .filter(
            AiTask.user_id == user_id,
            AiTask.dedupe_key == dedupe_key,
            AiTask.status.in_(["new", "in_progress"]),
        )
        .order_by(AiTask.created_at.desc())
        .first()
    )
    if existing is None:
        row = AiTask(
            user_id=user_id,
            nm_id=None,
            task_type="competitor_report_refresh",
            title="Обновить отчёт сравнения с конкурентами",
            description="Операция может быть платной/лимитной — требуется подтверждение",
            reason="competitor_report_validity_3d",
            current_value={"period": period},
            priority=50,
            status="new",
            fingerprint=None,
            dedupe_key=dedupe_key,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return AiTaskItem.model_validate(row)

    # Refresh explanation only
    existing.current_value = {"period": period}
    existing.title = "Обновить отчёт сравнения с конкурентами"
    existing.description = "Операция может быть платной/лимитной — требуется подтверждение"
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return AiTaskItem.model_validate(existing)


@router.post("/analytics/run", response_model=AiDailyAnalyticsRunResponse)
def ai_daily_analytics_run(
    body: AiDailyAnalyticsRunRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> AiDailyAnalyticsRunResponse:
    try:
        res = run_daily_analytics(
            db=db,
            user_id=str(store_ctx.store_owner.id),
            report_id=body.report_id,
            date_for=body.date_for,
            stock_days_left=body.stock_days_left,
            social=body.social,
        )
    except AnalyticsNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except AnalyticsInvalidPayloadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    return AiDailyAnalyticsRunResponse(
        status="ok",
        date_for=res.date_for,
        report_id=res.report_id,
        created_task_ids=res.created_task_ids,
        created_hypothesis_ids=res.created_hypothesis_ids,
    )

