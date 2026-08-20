"""Make hashed_password nullable for OAuth users

Revision ID: hashed_pw_nullable
Revises: add_oauth_fields
Create Date: 2026-05-20

The add_oauth_fields migration intended to make hashed_password nullable, but
the change never reached the database (the column-add operations were applied
out-of-band; the alter_column was missed). This migration applies the column
change idempotently so OAuth signup can insert users without a password.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'hashed_pw_nullable'
down_revision: Union[str, None] = 'add_oauth_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'users',
        'hashed_password',
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'users',
        'hashed_password',
        existing_type=sa.String(),
        nullable=False,
    )
