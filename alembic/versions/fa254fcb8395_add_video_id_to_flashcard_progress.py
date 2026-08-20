"""add_video_id_to_flashcard_progress

Revision ID: fa254fcb8395
Revises: b5b605ebe69b
Create Date: 2026-03-14 12:52:44.293168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fa254fcb8395'
down_revision: Union[str, None] = 'a4b63753908a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_flashcard_progress', sa.Column('video_id', sa.String(length=20), nullable=True))
    op.create_index(op.f('ix_user_flashcard_progress_video_id'), 'user_flashcard_progress', ['video_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_flashcard_progress_video_id'), table_name='user_flashcard_progress')
    op.drop_column('user_flashcard_progress', 'video_id')
