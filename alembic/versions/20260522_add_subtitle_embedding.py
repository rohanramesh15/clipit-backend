"""add subtitle_embedding table with pgvector

Revision ID: add_subtitle_embedding
Revises: add_lemma_column
Create Date: 2026-05-22 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = 'add_subtitle_embedding'
down_revision: Union[str, None] = 'add_lemma_column'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Extension is already enabled on the Neon project; this is a no-op if so.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Check if table exists
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='subtitle_embedding'"
    ))
    if result.fetchone() is None:
        op.create_table(
            'subtitle_embedding',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('video_id', sa.String(length=100), nullable=False),
            sa.Column('language', sa.String(length=10), nullable=False),
            sa.Column('sentence', sa.String(), nullable=False),
            sa.Column('sentence_translation', sa.String(), nullable=True),
            sa.Column('lemma_set', sa.JSON(), nullable=True),
            sa.Column('ts_start', sa.Float(), nullable=True),
            sa.Column('ts_end', sa.Float(), nullable=True),
            sa.Column('embedding', Vector(768), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )

    # Create indexes if they don't exist
    for idx_name in ['ix_subtitle_embedding_video_id', 'ix_subtitle_embedding_language', 'ix_subtitle_embedding_video_lang']:
        result = conn.execute(sa.text(f"SELECT indexname FROM pg_indexes WHERE indexname='{idx_name}'"))
        if result.fetchone() is None:
            if idx_name == 'ix_subtitle_embedding_video_id':
                op.create_index(idx_name, 'subtitle_embedding', ['video_id'])
            elif idx_name == 'ix_subtitle_embedding_language':
                op.create_index(idx_name, 'subtitle_embedding', ['language'])
            elif idx_name == 'ix_subtitle_embedding_video_lang':
                op.create_index(idx_name, 'subtitle_embedding', ['video_id', 'language'])

    # HNSW index for fast cosine similarity search
    result = conn.execute(sa.text("SELECT indexname FROM pg_indexes WHERE indexname='ix_subtitle_embedding_hnsw'"))
    if result.fetchone() is None:
        op.execute("""
            CREATE INDEX ix_subtitle_embedding_hnsw
            ON subtitle_embedding
            USING hnsw (embedding vector_cosine_ops)
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_subtitle_embedding_hnsw")
    op.drop_index('ix_subtitle_embedding_video_lang', table_name='subtitle_embedding')
    op.drop_index('ix_subtitle_embedding_language', table_name='subtitle_embedding')
    op.drop_index('ix_subtitle_embedding_video_id', table_name='subtitle_embedding')
    op.drop_table('subtitle_embedding')
