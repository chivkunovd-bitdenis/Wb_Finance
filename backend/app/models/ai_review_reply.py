from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class AiReviewReply(Base):
    __tablename__ = "ai_review_replies"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # WB feedback id (string in WB API)
    feedback_id = Column(String(64), nullable=False)

    # Snapshot fields for UI context
    product_name = Column(String(512), nullable=True)
    author = Column(String(255), nullable=True)
    rating = Column(String(16), nullable=True)  # keep as string to avoid WB format surprises
    review_text = Column(Text, nullable=True)

    suggested_reply = Column(Text, nullable=True)
    edited_reply = Column(Text, nullable=True)

    status = Column(String(24), nullable=False, default="pending")  # pending|published|skipped|error
    last_error = Column(Text, nullable=True)

    first_seen_date = Column(Date, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "feedback_id", name="uq_ai_review_replies_user_feedback"),
        CheckConstraint(
            "status in ('pending','published','skipped','error')",
            name="ck_ai_review_replies_status",
        ),
    )

