"""merge_subtitles_and_community

Revision ID: 2e763bc53e2e
Revises: add_subtitles_uk_col, add_community_tables
Create Date: 2026-06-02 15:32:00.671925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e763bc53e2e'
down_revision: Union[str, None] = ('add_subtitles_uk_col', 'add_community_tables')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
