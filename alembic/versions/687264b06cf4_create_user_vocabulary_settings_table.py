"""create user_vocabulary_settings table

Revision ID: 687264b06cf4
Revises: 627dfd022600
Create Date: 2026-08-18 19:32:13.561264

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '687264b06cf4'
down_revision: Union[str, None] = '627dfd022600'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_vocabulary_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('priority_mode', sa.String(length=20), nullable=False, server_default='mixed'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_vocabulary_settings_id'), 'user_vocabulary_settings', ['id'], unique=False)
    op.create_index(op.f('ix_user_vocabulary_settings_user_id'), 'user_vocabulary_settings', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_vocabulary_settings_user_id'), table_name='user_vocabulary_settings')
    op.drop_index(op.f('ix_user_vocabulary_settings_id'), table_name='user_vocabulary_settings')
    op.drop_table('user_vocabulary_settings')
