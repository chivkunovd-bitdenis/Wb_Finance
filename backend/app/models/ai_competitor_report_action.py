from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class AiCompetitorReportAction(Base):
    __tablename__ = "ai_competitor_report_actions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ai_competitor_comparison_reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action = Column(String(16), nullable=False)  # create|refresh
    result = Column(String(16), nullable=False, default="ok")  # ok|error
    error_message = Column(Text, nullable=True)

    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("action in ('create','refresh')", name="ck_ai_comp_report_actions_action"),
        CheckConstraint("result in ('ok','error')", name="ck_ai_comp_report_actions_result"),
    )

