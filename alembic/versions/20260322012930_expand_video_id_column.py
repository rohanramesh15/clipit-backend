"""Expand video_id column size

Revision ID: expand_video_id_col
Revises: fa254fcb8395
Create Date: 2026-03-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = 'expand_video_id_col'
down_revision: Union[str, None] = 'fa254fcb8395'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand video_id column from String(20) to String(64)
    op.alter_column('user_flashcard_progress', 'video_id',
                    existing_type=sa.String(20),
                    type_=sa.String(64),
                    existing_nullable=True)


def downgrade() -> None:
    op.alter_column('user_flashcard_progress', 'video_id',
                    existing_type=sa.String(64),
                    type_=sa.String(20),
                    existing_nullable=True)
