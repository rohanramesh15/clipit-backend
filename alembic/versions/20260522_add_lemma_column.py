"""add lemma column to user_flashcard_progress

Revision ID: add_lemma_column
Revises: add_has_spanish
Create Date: 2026-05-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_lemma_column'
down_revision: Union[str, None] = 'add_has_spanish'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Check if column exists
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='user_flashcard_progress' AND column_name='lemma'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'user_flashcard_progress',
            sa.Column('lemma', sa.String(), nullable=True),
        )
    # Check if index exists
    result = conn.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE indexname='ix_user_flashcard_progress_lemma'"
    ))
    if result.fetchone() is None:
        op.create_index(
            'ix_user_flashcard_progress_lemma',
            'user_flashcard_progress',
            ['lemma'],
        )


def downgrade() -> None:
    op.drop_index('ix_user_flashcard_progress_lemma', table_name='user_flashcard_progress')
    op.drop_column('user_flashcard_progress', 'lemma')
