"""grant lifetime licenses to all existing users

Revision ID: e1f2a3b4c5d6
Revises: d1a2b3c4e5f6
Create Date: 2026-03-27
"""
from alembic import op

revision = 'e1f2a3b4c5d6'
down_revision = 'd1a2b3c4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Всем пользователям у которых нет записи в licenses — вставить lifetime
    op.execute("""
        INSERT INTO licenses (id, user_id, status, valid_until, source, updated_at)
        SELECT
            gen_random_uuid(),
            u.id,
            'lifetime',
            NULL,
            'grandfathered',
            NOW()
        FROM users u
        LEFT JOIN licenses l ON l.user_id = u.id
        WHERE l.id IS NULL
    """)

    # Обновить тех у кого уже есть запись (trial/inactive/expired) → lifetime
    op.execute("""
        UPDATE licenses
        SET status = 'lifetime',
            valid_until = NULL,
            source = 'grandfathered',
            updated_at = NOW()
        WHERE status != 'lifetime'
    """)


def downgrade() -> None:
    # Убрать grandfathered lifetime — вернуть в inactive
    op.execute("""
        UPDATE licenses
        SET status = 'inactive',
            updated_at = NOW()
        WHERE source = 'grandfathered' AND status = 'lifetime'
    """)
    op.execute("""
        DELETE FROM licenses
        WHERE source = 'grandfathered'
    """)
