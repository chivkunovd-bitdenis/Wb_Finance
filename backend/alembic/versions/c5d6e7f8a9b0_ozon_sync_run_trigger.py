"""Record why an Ozon sync run was queued (manual, automatic backfill, or daily refresh).

Revision ID: c5d6e7f8a9b0
Revises: a9c4e2f7b1d3
Create Date: 2026-10-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "a9c4e2f7b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows were all queued by the owner's manual button, so the default is exact.
    op.add_column(
        "ozon_sync_runs",
        sa.Column("trigger", sa.String(length=16), server_default="manual", nullable=False),
    )
    op.create_check_constraint(
        "ck_ozon_sync_run_trigger",
        "ozon_sync_runs",
        "trigger in ('manual','backfill','daily')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ozon_sync_run_trigger", "ozon_sync_runs", type_="check")
    op.drop_column("ozon_sync_runs", "trigger")
