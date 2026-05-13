from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_fetch_reference_images_downloads_from_monolith(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIP_MONOLITH_BASE_URL", "http://api.test")
    monkeypatch.setenv("WIP_MONOLITH_REFERENCE_SECRET", "ref-secret")

    import app.config as cfg
    import app.services.reference_fetch_client as m

    importlib.reload(cfg)
    importlib.reload(m)

    fake_resp = httpx.Response(
        200,
        content=b"ref-image",
        headers={
            "content-type": "image/png",
            "content-disposition": 'attachment; filename="polo.png"',
        },
    )
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_resp

    with patch("httpx.Client", return_value=fake_client):
        refs = m.fetch_reference_images(monolith_job_id="job-1", reference_asset_ids=["ref-1"])

    assert len(refs) == 1
    assert refs[0].asset_id == "ref-1"
    assert refs[0].filename == "polo.png"
    assert refs[0].content == b"ref-image"
    fake_client.get.assert_called_once()
    url = fake_client.get.call_args.args[0]
    assert url == "http://api.test/ai/product-generation/internal/jobs/job-1/references/ref-1/file"
    assert fake_client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer ref-secret"


def test_fetch_reference_images_raises_on_missing_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIP_MONOLITH_BASE_URL", "http://api.test")
    monkeypatch.setenv("WIP_MONOLITH_REFERENCE_SECRET", "ref-secret")

    import app.config as cfg
    import app.services.reference_fetch_client as m

    importlib.reload(cfg)
    importlib.reload(m)

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = httpx.Response(404, text="missing")

    with patch("httpx.Client", return_value=fake_client):
        with pytest.raises(m.ReferenceFetchError, match="not found"):
            m.fetch_reference_images(monolith_job_id="job-1", reference_asset_ids=["ref-1"])
