"""Index review history for the Progress daily aggregate.

Revision ID: f1a91d3b6e12
Revises: e2a7c5f9d431
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f1a91d3b6e12"
down_revision: Union[str, None] = "e2a7c5f9d431"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_user_review_history_user_id_reviewed_at",
        "user_review_history",
        ["user_id", "reviewed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_review_history_user_id_reviewed_at", table_name="user_review_history")
