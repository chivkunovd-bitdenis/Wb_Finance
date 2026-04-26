import app.env_bootstrap  # noqa: F401 — .env до чтения os.getenv ниже

import os

from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "wb_finance",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/0",
    include=["celery_app.tasks"],
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "Europe/Moscow"
celery_app.conf.enable_utc = True

_DAILY_BRIEF_ENABLED = (os.getenv("DAILY_BRIEF_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
_ARCHIVE_BACKFILL_ENABLED = (os.getenv("ARCHIVE_BACKFILL_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}

beat_schedule: dict = {
    # Ежедневные напоминания о платёжке.
    "billing-send-reminders-daily": {
        "task": "billing_send_reminders",
        "schedule": crontab(hour=9, minute=0),  # 09:00 UTC = 12:00 МСК
    },
}

if _DAILY_BRIEF_ENABLED:
    beat_schedule.update(
        {
            # Ежедневная AI-сводка: генерируем в 07:00 МСК (04:00 UTC).
            # К этому времени данные WB за вчера, как правило, уже доступны.
            "generate-daily-briefs-07-msk": {
                "task": "generate_all_daily_briefs",
                "schedule": crontab(hour=4, minute=0),  # 04:00 UTC = 07:00 МСК
            },
            # Retry-волна в 09:00 МСК (06:00 UTC) для случаев, когда данные задержались.
            "generate-daily-briefs-09-msk": {
                "task": "generate_all_daily_briefs",
                "schedule": crontab(hour=6, minute=0),  # 06:00 UTC = 09:00 МСК
            },
        }
    )

if _ARCHIVE_BACKFILL_ENABLED:
    beat_schedule.update(
        {
            # Dosed archive backfill manager: only kicks intents (no WB calls).
            "archive-backfill-manager-every-10-min": {
                "task": "archive_backfill_manager",
                "schedule": crontab(minute="*/10"),
            },
        }
    )

celery_app.conf.beat_schedule = beat_schedule
