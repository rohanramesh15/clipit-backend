"""Merge migration heads

Revision ID: 4e8528666160
Revises: 3e4e8ca7d1a4, bd0f591d96d7
Create Date: 2026-03-01 10:56:12.540945

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e8528666160'
down_revision: Union[str, None] = ('3e4e8ca7d1a4', 'bd0f591d96d7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
