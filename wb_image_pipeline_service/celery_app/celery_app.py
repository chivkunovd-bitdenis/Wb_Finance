"""Celery application: queues and image pipeline workers live in this service."""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "wb_image_pipeline",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="wb_image_pipeline.ping")
def ping() -> str:
    return "pong"
