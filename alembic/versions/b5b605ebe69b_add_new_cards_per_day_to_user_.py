"""add new_cards_per_day to user_vocabulary_settings

Revision ID: b5b605ebe69b
Revises: 687264b06cf4
Create Date: 2026-03-14 12:31:10.015185

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5b605ebe69b'
down_revision: Union[str, None] = '687264b06cf4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_vocabulary_settings', sa.Column('new_cards_per_day', sa.Integer(), nullable=False, server_default='10'))


def downgrade() -> None:
    op.drop_column('user_vocabulary_settings', 'new_cards_per_day')
