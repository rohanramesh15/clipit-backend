"""create user_vocabulary_words table

Revision ID: e8b5caa6547a
Revises: cc51bba0c21b
Create Date: 2026-08-18 19:35:48.970453

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b5caa6547a'
down_revision: Union[str, None] = 'cc51bba0c21b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_vocabulary_words',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('list_id', sa.Integer(), nullable=False),
    sa.Column('word', sa.String(length=100), nullable=False),
    sa.Column('translation', sa.String(length=500), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['list_id'], ['user_vocabulary_lists.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('list_id', 'word', name='uq_vocab_list_word')
    )
    op.create_index(op.f('ix_user_vocabulary_words_id'), 'user_vocabulary_words', ['id'], unique=False)
    op.create_index(op.f('ix_user_vocabulary_words_list_id'), 'user_vocabulary_words', ['list_id'], unique=False)
    op.create_index(op.f('ix_user_vocabulary_words_word'), 'user_vocabulary_words', ['word'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_vocabulary_words_word'), table_name='user_vocabulary_words')
    op.drop_index(op.f('ix_user_vocabulary_words_list_id'), table_name='user_vocabulary_words')
    op.drop_index(op.f('ix_user_vocabulary_words_id'), table_name='user_vocabulary_words')
    op.drop_table('user_vocabulary_words')
