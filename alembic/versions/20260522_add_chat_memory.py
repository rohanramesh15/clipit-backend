"""add chat_memory_fact table and user_language_profile table

Revision ID: add_chat_memory
Revises: add_chat_tables
Create Date: 2026-05-22 00:03:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = 'add_chat_memory'
down_revision: Union[str, None] = 'add_chat_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    def table_exists(name):
        result = conn.execute(sa.text(
            f"SELECT table_name FROM information_schema.tables WHERE table_name='{name}'"
        ))
        return result.fetchone() is not None

    def index_exists(name):
        result = conn.execute(sa.text(f"SELECT indexname FROM pg_indexes WHERE indexname='{name}'"))
        return result.fetchone() is not None

    # ── Memory facts (cross-session memory) ─────────────────────────────────
    if not table_exists('chat_memory_fact'):
        op.create_table(
            'chat_memory_fact',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('language', sa.String(length=10), nullable=False, server_default='es'),
            sa.Column('fact', sa.Text(), nullable=False),
            sa.Column('source_session_id', sa.Integer(),
                      sa.ForeignKey('chat_session.id', ondelete='SET NULL'), nullable=True),
            sa.Column('embedding', Vector(768), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
    if not index_exists('ix_chat_memory_fact_user_id'):
        op.create_index('ix_chat_memory_fact_user_id', 'chat_memory_fact', ['user_id'])
    if not index_exists('ix_chat_memory_fact_user_lang'):
        op.create_index('ix_chat_memory_fact_user_lang', 'chat_memory_fact', ['user_id', 'language'])
    if not index_exists('ix_chat_memory_fact_hnsw'):
        op.execute("""
            CREATE INDEX ix_chat_memory_fact_hnsw
            ON chat_memory_fact
            USING hnsw (embedding vector_cosine_ops)
        """)

    # ── Dual proficiency tracking ────────────────────────────────────────────
    if not table_exists('user_language_profile'):
        op.create_table(
            'user_language_profile',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('language', sa.String(length=10), nullable=False),
            sa.Column('self_rated_level', sa.String(length=8), nullable=True),
            sa.Column('recognition_score', sa.Float(), nullable=True),  # 0-6 CEFR-ish, from FSRS
            sa.Column('production_score', sa.Float(), nullable=True),   # 0-6 from chat rubrics
            sa.Column('session_count', sa.Integer(), nullable=False, server_default='0'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'language', name='uq_user_language_profile'),
        )
    if not index_exists('ix_user_language_profile_user_id'):
        op.create_index('ix_user_language_profile_user_id', 'user_language_profile', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_language_profile_user_id', table_name='user_language_profile')
    op.drop_table('user_language_profile')

    op.execute("DROP INDEX IF EXISTS ix_chat_memory_fact_hnsw")
    op.drop_index('ix_chat_memory_fact_user_lang', table_name='chat_memory_fact')
    op.drop_index('ix_chat_memory_fact_user_id', table_name='chat_memory_fact')
    op.drop_table('chat_memory_fact')
