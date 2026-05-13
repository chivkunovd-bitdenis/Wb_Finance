from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_SVC_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {**os.environ, "WIP_DATABASE_URL": db_url, "PYTHONPATH": str(_SVC_ROOT)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_SVC_ROOT,
        env=env,
        check=True,
    )


def _reload_app_stack() -> None:
    import app.api.internal_runs as ir
    import app.config as cfg
    import app.deps as dps
    import app.db as dbm
    import app.main as mm

    importlib.reload(cfg)
    importlib.reload(dbm)
    importlib.reload(dps)
    importlib.reload(ir)
    importlib.reload(mm)


@pytest.fixture
def http_runs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    db_url = f"sqlite:///{tmp_path / 'wip_http.db'}"
    monkeypatch.setenv("WIP_DATABASE_URL", db_url)
    monkeypatch.setenv("WIP_REDIS_URL", "redis://127.0.0.1:6379/14")
    monkeypatch.setenv("WIP_INTERNAL_HMAC_SECRET", "test-internal-secret")
    sys.path.insert(0, str(_SVC_ROOT))

    _run_alembic_upgrade(db_url)
    _reload_app_stack()

    return db_url


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-internal-secret"}


def test_internal_runs_post_get_mock_enqueue(http_runs_env: str) -> None:
    with patch("app.api.internal_runs.enqueue_pg32_stub_chain") as enq:
        enq.return_value = None
        import app.main as mm

        client = TestClient(mm.app)
        res = client.post(
            "/internal/v1/runs",
            json={"monolith_job_id": "job-abc-1", "payload": {"refs": [1]}},
            headers=_auth_headers(),
        )
        assert res.status_code == 201
        data = res.json()
        run_id = data["id"]
        assert data["status"] == "created"
        enq.assert_called_once_with(run_id)

        got = client.get(f"/internal/v1/runs/{run_id}", headers=_auth_headers())
        assert got.status_code == 200
        body = got.json()
        assert body["id"] == run_id
        assert body["status"] == "created"
        assert body["monolith_job_id"] == "job-abc-1"
        assert body["payload"] == {"refs": [1]}
        assert body["steps"] == []
        assert body["assets"] == []


def test_internal_runs_post_enqueue_503(http_runs_env: str) -> None:
    with patch("app.api.internal_runs.enqueue_pg32_stub_chain") as enq:
        enq.side_effect = RuntimeError("broker down")
        import app.main as mm

        client = TestClient(mm.app)
        res = client.post(
            "/internal/v1/runs",
            json={"monolith_job_id": "job-x"},
            headers=_auth_headers(),
        )
        assert res.status_code == 503


def test_internal_runs_get_404(http_runs_env: str) -> None:
    import app.main as mm

    client = TestClient(mm.app)
    res = client.get(
        "/internal/v1/runs/00000000-0000-0000-0000-000000000099",
        headers=_auth_headers(),
    )
    assert res.status_code == 404


def test_internal_runs_unauthorized(http_runs_env: str) -> None:
    import app.main as mm

    client = TestClient(mm.app)
    res = client.post("/internal/v1/runs", json={"monolith_job_id": "j1"})
    assert res.status_code == 401


def test_internal_runs_get_reflects_pg32_chain_after_manual_enqueue(http_runs_env: str) -> None:
    """POST мокает постановку в брокер; затем тот же chain, что и enqueue_pg32_stub_chain, в eager-режиме."""
    import importlib

    import celery_app.celery_app as cap
    import celery_app.pipeline_tasks as pt

    importlib.reload(cap)
    importlib.reload(pt)
    from celery import chain
    from celery_app.celery_app import celery_app
    from celery_app.pipeline_tasks import run_created, step_done

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    import app.main as mm

    client = TestClient(mm.app)
    with patch("app.api.internal_runs.enqueue_pg32_stub_chain"):
        res = client.post(
            "/internal/v1/runs",
            json={"monolith_job_id": "job-eager"},
            headers=_auth_headers(),
        )
    assert res.status_code == 201
    run_id = res.json()["id"]

    chain(run_created.s(run_id), step_done.s()).apply_async().get(timeout=10)

    got = client.get(f"/internal/v1/runs/{run_id}", headers=_auth_headers())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "completed"
    assert len(body["steps"]) == 1
    assert body["steps"][0]["step_key"] == "pg32_stub"
    assert body["steps"][0]["status"] == "done"
