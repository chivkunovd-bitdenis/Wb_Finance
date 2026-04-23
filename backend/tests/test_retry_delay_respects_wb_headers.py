import random

import requests


def test_retry_delay_respects_x_ratelimit_reset(monkeypatch):
    from celery_app import tasks

    # Make jitter deterministic
    monkeypatch.setattr(random, "randint", lambda a, b: 0)

    resp = requests.Response()
    resp.status_code = 429
    resp.headers["X-RateLimit-Reset"] = "10000"

    delay = tasks._retry_http_delay_with_headers(429, 1, resp)
    assert delay >= 10000


def test_retry_delay_falls_back_without_headers(monkeypatch):
    from celery_app import tasks

    monkeypatch.setattr(random, "randint", lambda a, b: 0)

    resp = requests.Response()
    resp.status_code = 429
    # no headers

    delay = tasks._retry_http_delay_with_headers(429, 1, resp)
    assert delay >= tasks.FUNNEL_YTD_429_RETRY_BASE_SEC

