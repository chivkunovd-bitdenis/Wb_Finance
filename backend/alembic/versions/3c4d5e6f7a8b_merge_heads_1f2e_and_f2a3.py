"""merge alembic heads (finance_missing_sync_state + funnel_rolling_sync_state)

Revision ID: 3c4d5e6f7a8b
Revises: 1f2e3d4c5b6a, f2a3b4c5d6e7
Create Date: 2026-04-25

"""

from typing import Sequence, Union


revision: str = "3c4d5e6f7a8b"
down_revision: Union[str, tuple[str, str], None] = ("1f2e3d4c5b6a", "f2a3b4c5d6e7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # merge revision: no-op (only ties two heads into a single line)
    pass


def downgrade() -> None:
    # merge revision: no-op
    pass

