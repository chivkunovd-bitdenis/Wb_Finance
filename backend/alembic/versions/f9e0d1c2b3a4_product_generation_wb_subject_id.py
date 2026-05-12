"""product_generation_jobs: optional wb_subject_id

Revision ID: f9e0d1c2b3a4
Revises: 9e1f2a3b4c5d
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "f9e0d1c2b3a4"
down_revision: Union[str, None] = "9e1f2a3b4c5d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS: таблица могла появиться вне очереди alembic (тесты/create).
    op.execute(
        text("ALTER TABLE product_generation_jobs ADD COLUMN IF NOT EXISTS wb_subject_id INTEGER")
    )


def downgrade() -> None:
    op.execute(text("ALTER TABLE product_generation_jobs DROP COLUMN IF EXISTS wb_subject_id"))
