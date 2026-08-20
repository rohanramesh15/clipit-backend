"""Add example and example_translation columns to user_vocabulary_words

Revision ID: add_example_cols
Revises: add_prefer_tts_col
Create Date: 2026-04-01

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_example_cols'
down_revision = 'add_prefer_tts_col'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_vocabulary_words', sa.Column('example', sa.String(1000), nullable=True))
    op.add_column('user_vocabulary_words', sa.Column('example_translation', sa.String(1000), nullable=True))


def downgrade():
    op.drop_column('user_vocabulary_words', 'example_translation')
    op.drop_column('user_vocabulary_words', 'example')
