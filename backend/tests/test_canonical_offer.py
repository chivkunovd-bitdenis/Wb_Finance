from __future__ import annotations

import pytest

import app.services.canonical_offer as co


class _FakeState:
    def __init__(self, *, active_version: str | None, status: str) -> None:
        self.active_version = active_version
        self.status = status


class _FakeRedisLock:
    """Minimal SET NX EX + DELETE, enough for canonical_offer's lock usage."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


@pytest.fixture
def offer_pdf(tmp_path, monkeypatch):
    path = tmp_path / "offer_test.pdf"
    path.write_bytes(b"%PDF-1.4 canonical offer fixture content " * 20)
    monkeypatch.setenv("OFFER_CANONICAL_PATH", str(path))
    return path


def test_ensure_canonical_offer_indexed_skips_when_already_indexed(monkeypatch, offer_pdf):
    monkeypatch.setattr(co, "get_redis", lambda: _FakeRedisLock())
    monkeypatch.setattr(co, "count_version_points", lambda *, version: 5)
    monkeypatch.setattr(co, "get_offer_index_state", lambda: _FakeState(active_version=None, status="idle"))
    monkeypatch.setattr(co, "mark_ready", lambda **kw: None)

    called = {"indexed": False}

    def fake_index_offer_file(**kwargs):
        called["indexed"] = True
        return {"version": kwargs["version"], "chunks": 1, "deleted_old": 0}

    monkeypatch.setattr(co, "index_offer_file", fake_index_offer_file)

    version = co.ensure_canonical_offer_indexed()

    assert called["indexed"] is False
    assert version == co.compute_offer_version(offer_pdf.read_bytes())


def test_ensure_canonical_offer_indexed_indexes_when_not_yet_indexed(monkeypatch, offer_pdf):
    monkeypatch.setattr(co, "get_redis", lambda: _FakeRedisLock())
    monkeypatch.setattr(co, "count_version_points", lambda *, version: 0)
    monkeypatch.setattr(co, "get_offer_index_state", lambda: _FakeState(active_version=None, status="idle"))

    marked_ready = {}

    def fake_mark_ready(*, active_version):
        marked_ready["version"] = active_version

    monkeypatch.setattr(co, "mark_ready", fake_mark_ready)
    monkeypatch.setattr(co, "mark_failed", lambda **kw: None)

    called = {"indexed": False, "version": None}

    def fake_index_offer_file(**kwargs):
        called["indexed"] = True
        called["version"] = kwargs["version"]
        return {"version": kwargs["version"], "chunks": 3, "deleted_old": 0}

    monkeypatch.setattr(co, "index_offer_file", fake_index_offer_file)

    version = co.ensure_canonical_offer_indexed()

    assert called["indexed"] is True
    assert called["version"] == version
    assert marked_ready["version"] == version


def test_ensure_canonical_offer_indexed_releases_lock_on_failure(monkeypatch, offer_pdf):
    fake_redis = _FakeRedisLock()
    monkeypatch.setattr(co, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(co, "count_version_points", lambda *, version: 0)
    monkeypatch.setattr(co, "get_offer_index_state", lambda: _FakeState(active_version=None, status="idle"))
    monkeypatch.setattr(co, "mark_failed", lambda **kw: None)

    def fake_index_offer_file(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(co, "index_offer_file", fake_index_offer_file)

    with pytest.raises(RuntimeError):
        co.ensure_canonical_offer_indexed()

    assert co._LOCK_KEY not in fake_redis._data


def test_resolve_canonical_offer_path_picks_newest_pdf_when_no_override(tmp_path, monkeypatch):
    monkeypatch.delenv("OFFER_CANONICAL_PATH", raising=False)
    monkeypatch.setenv("OFFER_DATA_DIR", str(tmp_path))
    old = tmp_path / "offer_old.pdf"
    old.write_bytes(b"old")
    new = tmp_path / "offer_new.pdf"
    new.write_bytes(b"new")
    import time
    import os

    os.utime(old, (1, 1))
    os.utime(new, (time.time(), time.time()))

    resolved = co.resolve_canonical_offer_path()
    assert resolved == new
