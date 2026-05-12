from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class PipelineRun(Base):
    """
    Один прогон пайплайна (связка с монолитом — поле monolith_job_id, PG-3.4+).
    """

    __tablename__ = "wip_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    monolith_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="created")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    steps: Mapped[list["PipelineStep"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="PipelineStep.ordinal",
    )
    assets: Mapped[list["PipelineAsset"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('created', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_wip_runs_status",
        ),
    )


class PipelineStep(Base):
    __tablename__ = "wip_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wip_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[PipelineRun] = relationship(back_populates="steps")
    assets: Mapped[list["PipelineAsset"]] = relationship(back_populates="step")

    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'done', 'failed', 'skipped')",
            name="ck_wip_steps_status",
        ),
    )


class PipelineAsset(Base):
    __tablename__ = "wip_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wip_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("wip_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_rel_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[PipelineRun] = relationship(back_populates="assets")
    step: Mapped[PipelineStep | None] = relationship(back_populates="assets")
