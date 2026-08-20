"""Add subtitles_uk column to tracked_videos

Revision ID: add_subtitles_uk_col
Revises: hashed_pw_nullable
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_subtitles_uk_col'
down_revision: Union[str, None] = 'hashed_pw_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='tracked_videos' AND column_name='subtitles_uk'"
    ))
    if result.fetchone() is None:
        op.add_column('tracked_videos', sa.Column('subtitles_uk', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('tracked_videos', 'subtitles_uk')
