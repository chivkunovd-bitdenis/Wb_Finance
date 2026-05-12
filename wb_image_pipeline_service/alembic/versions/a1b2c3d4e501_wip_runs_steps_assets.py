"""wip_runs, wip_steps, wip_assets

Revision ID: a1b2c3d4e501
Revises:
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e501"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wip_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("monolith_job_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="created", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "status in ('created', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_wip_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wip_runs_monolith_job_id", "wip_runs", ["monolith_job_id"], unique=False)

    op.create_table(
        "wip_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'done', 'failed', 'skipped')",
            name="ck_wip_steps_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["wip_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wip_steps_run_id", "wip_steps", ["run_id"], unique=False)

    op.create_table(
        "wip_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("storage_rel_path", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["wip_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["wip_steps.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wip_assets_run_id", "wip_assets", ["run_id"], unique=False)
    op.create_index("ix_wip_assets_step_id", "wip_assets", ["step_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_wip_assets_step_id", table_name="wip_assets")
    op.drop_index("ix_wip_assets_run_id", table_name="wip_assets")
    op.drop_table("wip_assets")
    op.drop_index("ix_wip_steps_run_id", table_name="wip_steps")
    op.drop_table("wip_steps")
    op.drop_index("ix_wip_runs_monolith_job_id", table_name="wip_runs")
    op.drop_table("wip_runs")
