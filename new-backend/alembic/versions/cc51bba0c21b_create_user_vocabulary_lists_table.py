"""create user_vocabulary_lists table

Revision ID: cc51bba0c21b
Revises: add_card_type_col
Create Date: 2026-08-18 19:35:48.250986

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc51bba0c21b'
down_revision: Union[str, None] = 'add_card_type_col'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_vocabulary_lists',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('language', sa.String(length=10), nullable=False, server_default='ko'),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_vocabulary_lists_id'), 'user_vocabulary_lists', ['id'], unique=False)
    op.create_index(op.f('ix_user_vocabulary_lists_user_id'), 'user_vocabulary_lists', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_vocabulary_lists_user_id'), table_name='user_vocabulary_lists')
    op.drop_index(op.f('ix_user_vocabulary_lists_id'), table_name='user_vocabulary_lists')
    op.drop_table('user_vocabulary_lists')
