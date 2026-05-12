"""ai review replies: review_created_at

Revision ID: 2d4f3a1c0b11
Revises: df7a5419503d
Create Date: 2026-05-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2d4f3a1c0b11"
down_revision: Union[str, None] = "df7a5419503d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("ai_review_replies"):
        return
    existing = {c.get("name") for c in inspector.get_columns("ai_review_replies")}
    if "review_created_at" in existing:
        return
    op.add_column("ai_review_replies", sa.Column("review_created_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("ai_review_replies"):
        return
    existing = {c.get("name") for c in inspector.get_columns("ai_review_replies")}
    if "review_created_at" not in existing:
        return
    op.drop_column("ai_review_replies", "review_created_at")

