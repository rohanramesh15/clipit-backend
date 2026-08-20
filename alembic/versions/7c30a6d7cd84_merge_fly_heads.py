"""merge fly heads

Revision ID: 7c30a6d7cd84
Revises: add_chat_mode, 2e763bc53e2e
Create Date: 2026-06-03 00:20:18.393088

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c30a6d7cd84'
down_revision: Union[str, None] = ('add_chat_mode', '2e763bc53e2e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
