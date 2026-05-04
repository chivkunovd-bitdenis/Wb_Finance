from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.core.redis_client import get_redis


_REDIS_KEY = "offer_ai:index_state"


@dataclass(frozen=True)
class OfferIndexState:
    status: str  # idle|indexing|ready|failed
    active_version: str | None
    indexed_at: str | None
    error_message: str | None


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def get_offer_index_state() -> OfferIndexState:
    r = get_redis()
    raw = r.get(_REDIS_KEY)
    if not raw:
        return OfferIndexState(status="idle", active_version=None, indexed_at=None, error_message=None)
    try:
        data = json.loads(raw)
    except Exception:
        return OfferIndexState(status="idle", active_version=None, indexed_at=None, error_message="state_corrupted")
    return OfferIndexState(
        status=str(data.get("status") or "idle"),
        active_version=(str(data["active_version"]) if data.get("active_version") else None),
        indexed_at=(str(data["indexed_at"]) if data.get("indexed_at") else None),
        error_message=(str(data["error_message"]) if data.get("error_message") else None),
    )


def set_offer_index_state(state: OfferIndexState) -> None:
    r = get_redis()
    r.set(_REDIS_KEY, json.dumps(asdict(state), ensure_ascii=False))


def mark_indexing(*, next_version: str) -> OfferIndexState:
    prev = get_offer_index_state()
    state = OfferIndexState(
        status="indexing",
        active_version=prev.active_version,
        indexed_at=prev.indexed_at,
        error_message=None,
    )
    set_offer_index_state(state)
    return state


def mark_ready(*, active_version: str) -> OfferIndexState:
    state = OfferIndexState(
        status="ready",
        active_version=active_version,
        indexed_at=_utcnow_iso(),
        error_message=None,
    )
    set_offer_index_state(state)
    return state


def mark_failed(*, error_message: str) -> OfferIndexState:
    prev = get_offer_index_state()
    state = OfferIndexState(
        status="failed",
        active_version=prev.active_version,
        indexed_at=prev.indexed_at,
        error_message=error_message[:900],
    )
    set_offer_index_state(state)
    return state

