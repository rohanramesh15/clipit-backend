"""Stub migration for missing add_has_english revision.

This migration was referenced in the production database but the original
file was lost. This stub allows Alembic to recognize the revision.

Revision ID: add_has_english
Revises: hashed_pw_nullable
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_has_english'
down_revision: Union[str, None] = 'hashed_pw_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Original file was lost; production already has this column from the
    # lost migration, so guard for fresh databases that don't yet have it.
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='tracked_videos' AND column_name='has_english'"
    ))
    if result.fetchone() is None:
        op.add_column('tracked_videos', sa.Column('has_english', sa.Boolean(), nullable=True))


def downgrade() -> None:
    # Stub migration - no changes needed
    pass
