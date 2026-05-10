from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class AiTask(Base):
    __tablename__ = "ai_tasks"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    nm_id = Column(Integer, nullable=True, index=True)
    task_type = Column(String(64), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)

    source_metrics = Column(JSONB, nullable=True)
    threshold = Column(JSONB, nullable=True)
    current_value = Column(JSONB, nullable=True)
    competitor_median_value = Column(JSONB, nullable=True)

    priority = Column(Integer, nullable=False, default=0)  # higher = more important
    status = Column(String(16), nullable=False, default="new")  # new|in_progress|completed|cancelled

    due_date = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status in ('new', 'in_progress', 'completed', 'cancelled')",
            name="ck_ai_tasks_status",
        ),
        CheckConstraint("priority >= 0", name="ck_ai_tasks_priority_nonneg"),
    )

