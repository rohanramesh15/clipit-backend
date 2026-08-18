"""create user_flashcard_progress table

Revision ID: a4b63753908a
Revises: b5b605ebe69b
Create Date: 2026-08-18 19:35:47.585119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b63753908a'
down_revision: Union[str, None] = 'b5b605ebe69b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_flashcard_progress',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('word', sa.String(), nullable=False),
    sa.Column('language', sa.String(), nullable=False),
    sa.Column('due', sa.DateTime(), nullable=False),
    sa.Column('stability', sa.Float(), nullable=True),
    sa.Column('difficulty', sa.Float(), nullable=True),
    sa.Column('elapsed_days', sa.Integer(), nullable=True),
    sa.Column('scheduled_days', sa.Integer(), nullable=True),
    sa.Column('reps', sa.Integer(), nullable=True),
    sa.Column('lapses', sa.Integer(), nullable=True),
    sa.Column('state', sa.Integer(), nullable=True),
    sa.Column('last_review', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'word', 'language')
    )
    op.create_index(op.f('ix_user_flashcard_progress_id'), 'user_flashcard_progress', ['id'], unique=False)
    op.create_index(op.f('ix_user_flashcard_progress_user_id'), 'user_flashcard_progress', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_flashcard_progress_user_id'), table_name='user_flashcard_progress')
    op.drop_index(op.f('ix_user_flashcard_progress_id'), table_name='user_flashcard_progress')
    op.drop_table('user_flashcard_progress')
