"""Durable per-seller scheduling; the broker only carries immediate wakeups."""
import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.wb_orchestrator_state import WbOrchestratorState

DISPATCH_LEASE_SECONDS = 300


def utcnow() -> datetime:
    return datetime.now(UTC)


def utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def pending_ops(intents: dict | None) -> set[str]:
    high, low = (intents or {}).get("high") or {}, (intents or {}).get("low") or {}
    ops = set()
    fr = high.get("finance_range")
    if isinstance(fr, dict) and fr.get("date_from") and fr.get("date_to"):
        ops.add("finance")
    if low.get("finance_backfill_year") in (2025, 2026):
        ops.add("finance")
    if high.get("funnel_tail") is True:
        ops.add("funnel")
    return ops


def due_for_state(st: WbOrchestratorState, now: datetime, *, delay: int = 0) -> datetime | None:
    ops = pending_ops(st.intents)
    if not ops:
        return None
    blocked = (st.intents or {}).get("blocked") or {}
    floor = now + timedelta(seconds=delay)
    # Legacy rows may have a global cooldown without per-operation metadata.
    legacy = utc(st.cooldown_until) if not blocked else None
    return min(max(floor, utc(blocked.get(op)) or legacy or floor) for op in ops)


def locked_state(db: Session, user_id: str, *, create: bool = False) -> WbOrchestratorState | None:
    if create:
        db.execute(insert(WbOrchestratorState).values(user_id=user_id, status="idle", intents={})
                   .on_conflict_do_nothing(index_elements=["user_id"]))
    return (db.query(WbOrchestratorState).filter(WbOrchestratorState.user_id == user_id)
            .populate_existing().with_for_update().first())


def schedule_state(st: WbOrchestratorState, now: datetime, *, delay: int = 0) -> datetime | None:
    due = due_for_state(st, now, delay=delay)
    st.next_tick_at = due
    if due is None:
        st.status, st.cooldown_until = "idle", None
    elif due > now + timedelta(seconds=delay):
        st.status, st.cooldown_until = "cooldown", due.isoformat()
    else:
        st.status, st.cooldown_until = "scheduled", None
    return due


@contextmanager
def execution_lock(db: Session, user_id: str) -> Iterator[bool]:
    """Session lock survives commits inside WB import helpers. Never return it to pool held."""
    key = int.from_bytes(hashlib.blake2b(("wb-orchestrator:" + user_id).encode(), digest_size=8).digest(),
                         "big", signed=True)
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    with engine.connect() as conn:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
        conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    conn.commit()
                except Exception:
                    conn.invalidate()
                    raise


def claim_due(db: Session, *, now: datetime | None = None, limit: int = 100) -> list[tuple[str, str]]:
    now = now or utcnow()
    expired = WbOrchestratorState.dispatch_lease_until <= now
    rows = (db.query(WbOrchestratorState).filter(
        or_(WbOrchestratorState.next_tick_at <= now, expired),
        or_(WbOrchestratorState.dispatch_lease_until.is_(None), expired),
    ).order_by(WbOrchestratorState.next_tick_at.asc().nullsfirst())
        .with_for_update(skip_locked=True).limit(limit).all())
    claims = []
    for st in rows:
        due = due_for_state(st, now)
        if due is None or due > now:
            schedule_state(st, now)
            st.dispatch_token = st.dispatch_lease_until = None
            continue
        token = str(uuid.uuid4())
        st.dispatch_token = token
        st.dispatch_lease_until = now + timedelta(seconds=DISPATCH_LEASE_SECONDS)
        claims.append((str(st.user_id), token))
    db.commit()
    return claims


def release_failed_publish(db: Session, user_id: str, token: str) -> None:
    st = locked_state(db, user_id)
    if st and st.dispatch_token == token:
        st.dispatch_token = st.dispatch_lease_until = None
        # Keep/recreate durable due time even if a previous worker died after claim.
        if st.next_tick_at is None:
            schedule_state(st, utcnow())
    db.commit()
