"""add has_spanish column

Revision ID: add_has_spanish
Revises: hashed_pw_nullable
Create Date: 2026-05-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_has_spanish'
down_revision: Union[str, None] = 'hashed_pw_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if column already exists before adding
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='tracked_videos' AND column_name='has_spanish'"
    ))
    if result.fetchone() is None:
        op.add_column('tracked_videos', sa.Column('has_spanish', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('tracked_videos', 'has_spanish')
