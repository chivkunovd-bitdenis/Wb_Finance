import requests


def _http_error(status_code: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = b"{}"  # type: ignore[attr-defined]
    resp.url = "https://example.test/wb"
    return requests.HTTPError(f"{status_code} rate limited", response=resp)


def test_sync_funnel_schedules_retry_on_429(monkeypatch, real_db_session):
    from app.models.user import User
    from app.models.article import Article
    from celery_app import tasks

    u = User(email="u429@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()
    # Ensure nm_ids is non-empty, otherwise sync_funnel returns ok=True,count=0 early.
    real_db_session.add(Article(user_id=u.id, nm_id=123))
    real_db_session.commit()

    # Use the test DB session for the task (avoid touching real SessionLocal engine).
    monkeypatch.setattr(tasks, "SessionLocal", lambda: real_db_session)

    # Force WB client to raise 429 (patch the exact callable used inside tasks.py).
    def _raise_429(*args, **kwargs):
        raise _http_error(429)

    monkeypatch.setattr(tasks, "fetch_funnel", _raise_429)

    captured: dict = {}

    def _apply_async(*, kwargs, countdown):
        captured["kwargs"] = kwargs
        captured["countdown"] = countdown

    monkeypatch.setattr(tasks.sync_funnel, "apply_async", _apply_async)

    res = tasks.sync_funnel(str(u.id), "2026-04-01", "2026-04-02")

    assert res["ok"] is False
    assert res["error"] == "wb_retry_scheduled"
    assert res["http_code"] == 429
    assert "delay_sec" in res
    assert captured["kwargs"]["user_id"] == str(u.id)
    assert captured["kwargs"]["date_from"] == "2026-04-01"
    assert captured["kwargs"]["date_to"] == "2026-04-02"
    assert "retry_raw" in captured["kwargs"]

