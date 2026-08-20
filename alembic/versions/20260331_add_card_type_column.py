"""Add card_type column to user_flashcard_progress

Revision ID: add_card_type_col
Revises: expand_video_id_col
Create Date: 2026-03-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = 'add_card_type_col'
down_revision: Union[str, None] = 'expand_video_id_col'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add card_type column with default 'video' for existing cards
    op.add_column('user_flashcard_progress',
                  sa.Column('card_type', sa.String(length=20), nullable=False, server_default='video'))


def downgrade() -> None:
    op.drop_column('user_flashcard_progress', 'card_type')
