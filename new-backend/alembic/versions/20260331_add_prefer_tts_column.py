"""Add prefer_tts column to user_vocabulary_words

Revision ID: add_prefer_tts_col
Revises: add_card_type_col
Create Date: 2026-03-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = 'add_prefer_tts_col'
down_revision: Union[str, None] = 'e8b5caa6547a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add prefer_tts column with default False for existing words
    op.add_column('user_vocabulary_words',
                  sa.Column('prefer_tts', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('user_vocabulary_words', 'prefer_tts')
