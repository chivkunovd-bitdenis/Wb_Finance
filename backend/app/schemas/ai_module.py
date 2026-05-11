from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class AiTaskItem(BaseModel):
    id: str
    nm_id: int | None
    task_type: str
    title: str
    description: str | None
    reason: str | None
    source_metrics: dict[str, Any] | None
    threshold: dict[str, Any] | None
    current_value: dict[str, Any] | None
    competitor_median_value: dict[str, Any] | None
    priority: int
    status: str
    due_date: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class AiTaskListResponse(BaseModel):
    items: list[AiTaskItem]


class AiTaskUpdateRequest(BaseModel):
    status: str = Field(..., description="new|in_progress|completed|cancelled")


class AiHypothesisItem(BaseModel):
    id: str
    nm_id: int | None
    hypothesis_type: str
    title: str
    description: str | None
    goal: str | None
    trigger_reason: str | None
    baseline_metrics: dict[str, Any] | None
    competitor_median_metrics: dict[str, Any] | None
    expected_effect: dict[str, Any] | None
    test_period_days: int | None
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    daily_log: list[dict[str, Any]] | dict[str, Any] | None
    result_metrics: dict[str, Any] | None
    result_summary: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class AiHypothesisListResponse(BaseModel):
    items: list[AiHypothesisItem]


class AiHypothesisStartResponse(BaseModel):
    status: str


class AiHypothesisFinishRequest(BaseModel):
    result_summary: str | None = None


class AiHypothesisFinishResponse(BaseModel):
    status: str


class AiHypothesisDailyLogUpsertRequest(BaseModel):
    day: date
    happened: str | None = None
    changed: str | None = None
    unchanged: str | None = None


class AiHypothesisDailyLogItem(BaseModel):
    day: date
    happened: str | None
    changed: str | None
    unchanged: str | None
    created_at: datetime | None
    updated_at: datetime | None


class AiHypothesisDailyLogResponse(BaseModel):
    items: list[AiHypothesisDailyLogItem]


class AiCompetitorMetricImportItem(BaseModel):
    nm_id: int
    metric_code: str = Field(
        ...,
        description=(
            "ctr|traffic|funnel_cart|funnel_order — из Excel «Показатели»: "
            "Показы (абсолют) — по конкурентам среднее; "
            "Конверсия в корзину/заказ, % и CTR — п.п. как в ячейке WB, по конкурентам медиана."
        ),
    )
    our_value: float | None = None
    competitor_median_value: float | None = None
    unit: str | None = None
    extra: dict[str, Any] | None = None


class AiCompetitorReportImportRequest(BaseModel):
    report_date: date
    period: str = Field("unknown", description="week|month|quarter|unknown")
    source: str = Field("manual", description="manual|playwright")
    raw_payload: dict[str, Any] | None = None
    items: list[AiCompetitorMetricImportItem] = Field(default_factory=list)


class AiCompetitorReportItem(BaseModel):
    id: str
    report_date: date
    period: str
    source: str
    latest_import_batch_id: str | None = None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class AiCompetitorReportListResponse(BaseModel):
    items: list[AiCompetitorReportItem]


class AiCompetitorMetricItem(BaseModel):
    id: str
    nm_id: int
    metric_code: str
    import_batch_id: str
    our_value: float | None
    competitor_median_value: float | None
    unit: str | None
    extra: dict[str, Any] | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class AiCompetitorReportDetailResponse(BaseModel):
    report: AiCompetitorReportItem
    metrics: list[AiCompetitorMetricItem]
    # Same object stored on import (Playwright meta, parser hints, etc.); not returned in list endpoint.
    raw_payload: dict[str, Any] | None = None


class AiDailyAnalyticsRunRequest(BaseModel):
    report_id: str
    date_for: date | None = None
    # Optional: data not yet persisted in DB
    stock_days_left: dict[int, int] | None = None
    social: dict[int, dict[str, float | int]] | None = None  # e.g. {123: {"reviews": 10, "rating": 4.2}}


class AiDailyAnalyticsRunResponse(BaseModel):
    status: str
    date_for: date
    report_id: str
    created_task_ids: list[str]
    created_hypothesis_ids: list[str]


class AiWbCredentialsUpsertRequest(BaseModel):
    wb_login: str
    wb_password: str


class AiWbCredentialsStatusResponse(BaseModel):
    status: str  # active|invalid|needs_reauth|disabled|missing
    last_verified_at: datetime | None = None
    last_error: str | None = None


class AiTaskExecuteResponse(BaseModel):
    status: str
    task_id: str
    message: str | None = None


class AiCompetitorReportRefreshRequest(BaseModel):
    period: str = Field(..., description="week|month|quarter")


class AiCompetitorReportStatusResponse(BaseModel):
    status: str  # missing|ready|stale|running|error
    report_id: str | None = None
    report_date: date | None = None
    valid_until: date | None = None
    last_error: str | None = None


class AiCompetitorReportActionItem(BaseModel):
    id: str
    report_id: str | None
    action: str
    result: str
    error_message: str | None
    requested_at: datetime | None

    model_config = {"from_attributes": True}


class AiCompetitorReportActionListResponse(BaseModel):
    items: list[AiCompetitorReportActionItem]

