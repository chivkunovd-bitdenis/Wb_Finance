"""
Канонический файл оферты WB для нового unified ИИ-чата.

В отличие от старого admin-flow (backend/app/routers/offer_ai.py, upload по кнопке),
здесь оферта не загружается пользователями: в репозитории лежит один канонический файл
(backend/data/offers/offer_b9a0140c65bf6ac9.pdf), и RAG-инструмент чата (search_offer)
обязан сам убедиться, что этот файл проиндексирован в Qdrant, прежде чем делать retrieval.

ensure_canonical_offer_indexed() безопасен для конкурентных вызовов: если несколько
запросов /dashboard/assistant/ask прилетают одновременно на холодном старте (коллекция
Qdrant ещё пустая), только один из них реально индексирует файл — остальные ждут короткое
время и переиспользуют результат (redis lock, SET NX EX).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from app.core.redis_client import get_redis
from app.services.offer_index_state import get_offer_index_state, mark_failed, mark_ready
from app.services.offer_rag_service import compute_offer_version, count_version_points, index_offer_file

logger = logging.getLogger(__name__)

_LOCK_KEY = "assistant:canonical_offer_index:lock"
_LOCK_TTL_SEC = 120
_WAIT_POLL_SEC = 1.0
_WAIT_MAX_SEC = 90.0

_SUPPORTED_SUFFIXES = (".pdf", ".txt", ".html")


def _offer_dir() -> Path:
    """Тот же каталог, что использует старый offer upload flow (см. app/routers/offer_ai.py)."""
    base = (os.getenv("OFFER_DATA_DIR") or "/app/data/offers").strip()
    return Path(base)


def resolve_canonical_offer_path() -> Path:
    """
    Путь к каноническому файлу оферты.

    Приоритет:
      1. OFFER_CANONICAL_PATH (явный оверрайд, например для локального запуска/тестов).
      2. Единственный/новейший .pdf|.txt|.html в OFFER_DATA_DIR (тот же каталог, что и
         существующий upload-flow, по умолчанию backend/data/offers).
    """
    override = (os.getenv("OFFER_CANONICAL_PATH") or "").strip()
    if override:
        p = Path(override)
        if not p.is_file():
            raise FileNotFoundError(f"OFFER_CANONICAL_PATH указывает на несуществующий файл: {p}")
        return p

    offer_dir = _offer_dir()
    if not offer_dir.is_dir():
        raise FileNotFoundError(f"Каталог оферты не найден: {offer_dir}")

    candidates = sorted(
        (p for p in offer_dir.iterdir() if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"В каталоге {offer_dir} нет файла оферты (.pdf/.txt/.html)")
    return candidates[0]


def canonical_offer_version() -> str:
    path = resolve_canonical_offer_path()
    return compute_offer_version(path.read_bytes())


def _try_acquire_lock() -> bool:
    r = get_redis()
    try:
        return bool(r.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_SEC))
    except Exception:
        logger.exception("canonical_offer: failed to acquire redis lock, proceeding without it")
        return True


def _release_lock() -> None:
    r = get_redis()
    try:
        r.delete(_LOCK_KEY)
    except Exception:
        logger.exception("canonical_offer: failed to release redis lock")


def ensure_canonical_offer_indexed() -> str:
    """
    Убедиться, что канонический файл оферты проиндексирован в Qdrant, и вернуть его версию.

    Идемпотентно: если версия уже проиндексирована (есть точки в Qdrant для неё), просто
    возвращает версию без повторной индексации. Иначе индексирует под redis-локом.
    """
    version = canonical_offer_version()

    if count_version_points(version=version) > 0:
        st = get_offer_index_state()
        if st.active_version != version or st.status != "ready":
            mark_ready(active_version=version)
        return version

    got_lock = _try_acquire_lock()
    if not got_lock:
        # Кто-то другой уже индексирует — подождать и переиспользовать результат.
        waited = 0.0
        while waited < _WAIT_MAX_SEC:
            time.sleep(_WAIT_POLL_SEC)
            waited += _WAIT_POLL_SEC
            if count_version_points(version=version) > 0:
                return version
        # Не дождались — пробуем сами (лок мог протухнуть из-за упавшего воркера).
        got_lock = _try_acquire_lock()

    try:
        # Двойная проверка под локом: пока ждали лок, кто-то мог уже проиндексировать.
        if count_version_points(version=version) > 0:
            mark_ready(active_version=version)
            return version

        path = resolve_canonical_offer_path()
        prev_state = get_offer_index_state()
        prev_version = prev_state.active_version
        try:
            index_offer_file(file_path=str(path), version=version, prev_version=prev_version)
            mark_ready(active_version=version)
            logger.info("canonical_offer: indexed version=%s path=%s", version, path)
        except Exception as exc:
            mark_failed(error_message=str(exc))
            logger.exception("canonical_offer: indexing failed version=%s", version)
            raise
        return version
    finally:
        _release_lock()
