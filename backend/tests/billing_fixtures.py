"""Подписка/демо в тестах после WMS-1150 срез Д (402 без действующего доступа)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.models.license import License
from app.models.ozon import OzonConnection
from app.models.subscription import Subscription


def grant_active_trial(session: Session, user_id: str, *, days: float = 30) -> Subscription:
    """Действующий trial в реальной (транзакционной) PostgreSQL-сессии."""
    now = datetime.now(UTC)
    ends_at = now + timedelta(days=days)
    sub = session.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub is None:
        sub = Subscription(
            user_id=user_id,
            status="trial",
            trial_started_at=now - timedelta(days=1),
            trial_ends_at=ends_at,
            auto_renew=True,
            provider="yookassa",
        )
        session.add(sub)
    else:
        sub.status = "trial"
        sub.trial_started_at = now - timedelta(days=1)
        sub.trial_ends_at = ends_at
        sub.current_period_end = None
    session.flush()
    return sub


def wire_mock_session_billing(
    session: MagicMock,
    user_id: str,
    *,
    preactivated_trial: bool = False,
    fallback_query: Callable[..., MagicMock] | None = None,
) -> None:
    """
    Subscription/License/OzonConnection для get_current_user → require_access на MagicMock-сессии.
    При preactivated_trial=True доступ есть без WB-ключа (сценарии «нет API key» → 400, не 402).
    """
    now = datetime.now(UTC)
    sub = Subscription(
        user_id=user_id,
        status="inactive",
        auto_renew=True,
        provider="yookassa",
    )
    if preactivated_trial:
        sub.status = "trial"
        sub.trial_started_at = now - timedelta(days=1)
        sub.trial_ends_at = now + timedelta(days=30)

    holder: dict[str, Subscription] = {"sub": sub}

    def _billing_chain(first_value):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = first_value
        return chain

    prev = session.query.side_effect

    def _query(*models):
        if len(models) == 1:
            model = models[0]
            if model is Subscription:
                return _billing_chain(holder["sub"])
            if model is License:
                return _billing_chain(None)
            if model is OzonConnection:
                return _billing_chain(None)
        if fallback_query is not None:
            return fallback_query(*models)
        if prev is not None and not isinstance(prev, MagicMock):
            return prev(*models)
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        chain.first.return_value = None
        chain.all.return_value = []
        return chain

    session.query.side_effect = _query
    session.flush = MagicMock()
    if session.commit is None or not callable(getattr(session, "commit", None)):
        session.commit = MagicMock()
