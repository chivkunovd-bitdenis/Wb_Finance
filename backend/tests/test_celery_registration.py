"""
Smoke-тест: Celery воркер должен видеть наши задачи.

Это ловит ситуацию, когда контейнер воркера запущен со старым кодом и задачи
в очереди отбрасываются как "Received unregistered task".
"""


def test_celery_app_registers_main_tasks():
    from celery_app.celery import celery_app

    # задачи регистрируются по name=... в tasks.py
    expected = {
        "sync_sales",
        "sync_ads",
        "sync_funnel",
        "after_initial_sync_enqueue_funnel",
        "sync_funnel_ytd_step",
        "sync_finance_backfill_step",
        "recalculate_pnl",
        "recalculate_sku_daily",
        "billing_send_reminders",
    }
    registered = set(celery_app.tasks.keys())
    missing = expected - registered
    assert not missing, f"Celery tasks not registered: {sorted(missing)}"

