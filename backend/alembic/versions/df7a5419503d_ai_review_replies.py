"""ai review replies

Revision ID: df7a5419503d
Revises: f8a1c2d3e4b5
Create Date: 2026-05-12 10:13:06.305457

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df7a5419503d'
down_revision: Union[str, None] = 'f8a1c2d3e4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('ai_review_replies'):
        op.create_table(
            'ai_review_replies',
            sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
            sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
            sa.Column('feedback_id', sa.String(length=64), nullable=False),
            sa.Column('product_name', sa.String(length=512), nullable=True),
            sa.Column('author', sa.String(length=255), nullable=True),
            sa.Column('rating', sa.String(length=16), nullable=True),
            sa.Column('review_text', sa.Text(), nullable=True),
            sa.Column('suggested_reply', sa.Text(), nullable=True),
            sa.Column('edited_reply', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=24), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('first_seen_date', sa.Date(), nullable=False),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.CheckConstraint(
                "status in ('pending','published','skipped','error')",
                name='ck_ai_review_replies_status',
            ),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'feedback_id', name='uq_ai_review_replies_user_feedback'),
        )

    existing_indexes = {ix.get('name') for ix in inspector.get_indexes('ai_review_replies')}
    ix_name = op.f('ix_ai_review_replies_user_id')
    if ix_name not in existing_indexes:
        op.create_index(ix_name, 'ai_review_replies', ['user_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('ai_review_replies'):
        existing_indexes = {ix.get('name') for ix in inspector.get_indexes('ai_review_replies')}
        ix_name = op.f('ix_ai_review_replies_user_id')
        if ix_name in existing_indexes:
            op.drop_index(ix_name, table_name='ai_review_replies')
        op.drop_table('ai_review_replies')
