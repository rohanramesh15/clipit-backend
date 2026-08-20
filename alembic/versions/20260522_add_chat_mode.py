"""add mode column to chat_session

Revision ID: add_chat_mode
Revises: add_chat_memory
Create Date: 2026-05-22 00:04:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_chat_mode'
down_revision: Union[str, None] = 'add_chat_memory'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='chat_session' AND column_name='mode'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'chat_session',
            sa.Column('mode', sa.String(length=20), nullable=False, server_default='free'),
        )


def downgrade() -> None:
    op.drop_column('chat_session', 'mode')
