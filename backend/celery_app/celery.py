from celery import Celery

celery_app = Celery(
    "wb_finance",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/0",
    include=["celery_app.tasks"],
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
