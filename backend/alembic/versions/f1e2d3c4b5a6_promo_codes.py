"""promo_codes table with 10 initial codes

Revision ID: f1e2d3c4b5a6
Revises: e1f2a3b4c5d6
Create Date: 2026-03-27
"""
import secrets
import string
import uuid

from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql

revision = 'f1e2d3c4b5a6'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None

ALPHABET = string.ascii_uppercase + string.digits


def _gen_code() -> str:
    """Generate code like XXXX-XXXX-XXXX (16 chars + 2 dashes)."""
    parts = [''.join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3)]
    return '-'.join(parts)


def upgrade() -> None:
    op.create_table(
        'promo_codes',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('code', sa.String(32), nullable=False, unique=True, index=True),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('used_by_user_id', sa.dialects.postgresql.UUID(as_uuid=False), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Генерируем 10 уникальных кодов и вставляем
    codes = set()
    while len(codes) < 10:
        codes.add(_gen_code())

    conn = op.get_bind()
    for code in sorted(codes):
        conn.execute(
            sa.text(
                "INSERT INTO promo_codes (id, code, is_used) VALUES (:id, :code, false)"
            ),
            {"id": str(uuid.uuid4()), "code": code},
        )

    # Выводим коды в лог миграции чтобы они были видны сразу
    print("\n" + "=" * 50)
    print("PROMO CODES (скопируй и сохрани):")
    for code in sorted(codes):
        print(f"  {code}")
    print("=" * 50 + "\n")


def downgrade() -> None:
    op.drop_table('promo_codes')
