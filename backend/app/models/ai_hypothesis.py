from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class AiHypothesis(Base):
    __tablename__ = "ai_hypotheses"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    nm_id = Column(Integer, nullable=True, index=True)
    hypothesis_type = Column(String(64), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    goal = Column(Text, nullable=True)
    trigger_reason = Column(Text, nullable=True)

    baseline_metrics = Column(JSONB, nullable=True)
    competitor_median_metrics = Column(JSONB, nullable=True)
    expected_effect = Column(JSONB, nullable=True)

    # For idempotent creation by analytics (AI-MVP3): stable key to prevent duplicates.
    fingerprint = Column(String(80), nullable=True, index=True)

    # Dedupe key for "active hypothesis suppression": stable per (nm_id, hypothesis_type, etc.)
    dedupe_key = Column(String(120), nullable=True, index=True)

    test_period_days = Column(Integer, nullable=True)

    status = Column(String(16), nullable=False, default="draft")  # draft|running|finished|cancelled
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    daily_log = Column(JSONB, nullable=True)
    result_metrics = Column(JSONB, nullable=True)
    result_summary = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status in ('draft', 'running', 'finished', 'cancelled')",
            name="ck_ai_hypotheses_status",
        ),
        CheckConstraint("test_period_days is null or test_period_days > 0", name="ck_ai_hypotheses_period_pos"),
        UniqueConstraint("user_id", "fingerprint", name="uq_ai_hypotheses_user_fingerprint"),
    )

