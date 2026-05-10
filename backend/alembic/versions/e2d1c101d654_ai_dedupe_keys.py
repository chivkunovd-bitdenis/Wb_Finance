"""ai dedupe keys

Revision ID: e2d1c101d654
Revises: 2c3d4e5f6a70
Create Date: 2026-05-10 16:26:11.001652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e2d1c101d654"
down_revision: Union[str, None] = "2c3d4e5f6a70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_tasks", sa.Column("dedupe_key", sa.String(length=120), nullable=True))
    op.create_index("ix_ai_tasks_dedupe_key", "ai_tasks", ["dedupe_key"])

    op.add_column("ai_hypotheses", sa.Column("dedupe_key", sa.String(length=120), nullable=True))
    op.create_index("ix_ai_hypotheses_dedupe_key", "ai_hypotheses", ["dedupe_key"])

    # Dedupe rule: only one OPEN task per (user_id, dedupe_key)
    op.create_index(
        "ux_ai_tasks_user_dedupe_key_open",
        "ai_tasks",
        ["user_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key is not null AND status in ('new','in_progress')"),
    )

    # Dedupe rule: only one ACTIVE hypothesis per (user_id, dedupe_key)
    op.create_index(
        "ux_ai_hypotheses_user_dedupe_key_active",
        "ai_hypotheses",
        ["user_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key is not null AND status in ('draft','running')"),
    )


def downgrade() -> None:
    op.drop_index("ux_ai_hypotheses_user_dedupe_key_active", table_name="ai_hypotheses")
    op.drop_index("ix_ai_hypotheses_dedupe_key", table_name="ai_hypotheses")
    op.drop_column("ai_hypotheses", "dedupe_key")

    op.drop_index("ux_ai_tasks_user_dedupe_key_open", table_name="ai_tasks")
    op.drop_index("ix_ai_tasks_dedupe_key", table_name="ai_tasks")
    op.drop_column("ai_tasks", "dedupe_key")
