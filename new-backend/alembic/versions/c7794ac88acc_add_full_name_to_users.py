"""add full_name to users

Revision ID: c7794ac88acc
Revises: 32ac5c21cf3a
Create Date: 2026-08-18 19:44:17.605195

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7794ac88acc'
down_revision: Union[str, None] = '32ac5c21cf3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('full_name', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'full_name')
