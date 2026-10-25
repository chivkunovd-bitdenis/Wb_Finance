"""Общие фикстуры. Все файлы состояния создаются во временной папке pytest."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from support_fakes import FakeClock, FakeTelegram, make_config

from wbf_support_bot.config import Config
from wbf_support_bot.store import Store


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path / "state")


@pytest.fixture
def store(config: Config) -> Iterator[Store]:
    db = Store(config.db_path)
    yield db
    db.close()


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
