"""Make the deprecated users.username column nullable.

Revision ID: d9e3a1f7b624
Revises: c7794ac88acc
Create Date: 2026-08-18

Modern email/password and Google registration use email as the identity and
intentionally do not populate the legacy username field.  The SQLAlchemy model
has already marked the field nullable; this migration brings PostgreSQL in line
with that model.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9e3a1f7b624"
down_revision: Union[str, None] = "c7794ac88acc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(),
        nullable=False,
    )
