"""add chat tables

Revision ID: add_chat_tables
Revises: add_subtitle_embedding
Create Date: 2026-05-22 00:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_chat_tables'
down_revision: Union[str, None] = 'add_subtitle_embedding'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Helper to check if table exists
    def table_exists(name):
        result = conn.execute(sa.text(
            f"SELECT table_name FROM information_schema.tables WHERE table_name='{name}'"
        ))
        return result.fetchone() is not None

    # Helper to check if index exists
    def index_exists(name):
        result = conn.execute(sa.text(f"SELECT indexname FROM pg_indexes WHERE indexname='{name}'"))
        return result.fetchone() is not None

    if not table_exists('chat_session'):
        op.create_table(
            'chat_session',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('language', sa.String(length=10), nullable=False, server_default='es'),
            sa.Column('seed_type', sa.String(length=20), nullable=False, server_default='free'),
            sa.Column('seed_video_id', sa.String(length=100), nullable=True),
            sa.Column('seed_label', sa.String(length=255), nullable=True),
            sa.Column('level_used', sa.String(length=8), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('ended_at', sa.DateTime(), nullable=True),
            sa.Column('summary_json', sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    if not index_exists('ix_chat_session_user_id'):
        op.create_index('ix_chat_session_user_id', 'chat_session', ['user_id'])
    if not index_exists('ix_chat_session_seed_video_id'):
        op.create_index('ix_chat_session_seed_video_id', 'chat_session', ['seed_video_id'])

    if not table_exists('chat_turn'):
        op.create_table(
            'chat_turn',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('chat_session.id', ondelete='CASCADE'), nullable=False),
            sa.Column('idx', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=16), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('audio_url', sa.String(), nullable=True),
            sa.Column('lemmas_json', sa.JSON(), nullable=True),
            sa.Column('off_list_lemmas_json', sa.JSON(), nullable=True),
            sa.Column('tokens_in', sa.Integer(), nullable=True),
            sa.Column('tokens_out', sa.Integer(), nullable=True),
            sa.Column('latency_ms', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    if not index_exists('ix_chat_turn_session_id'):
        op.create_index('ix_chat_turn_session_id', 'chat_turn', ['session_id'])
    if not index_exists('ix_chat_turn_session_idx'):
        op.create_index('ix_chat_turn_session_idx', 'chat_turn', ['session_id', 'idx'])

    if not table_exists('chat_saved_word'):
        op.create_table(
            'chat_saved_word',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('chat_session.id', ondelete='CASCADE'), nullable=False),
            sa.Column('turn_id', sa.Integer(), sa.ForeignKey('chat_turn.id', ondelete='CASCADE'), nullable=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('lemma', sa.String(), nullable=False),
            sa.Column('surface_form', sa.String(), nullable=True),
            sa.Column('sentence', sa.Text(), nullable=True),
            sa.Column('fsrs_card_id', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    if not index_exists('ix_chat_saved_word_session_id'):
        op.create_index('ix_chat_saved_word_session_id', 'chat_saved_word', ['session_id'])
    if not index_exists('ix_chat_saved_word_user_id'):
        op.create_index('ix_chat_saved_word_user_id', 'chat_saved_word', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_saved_word_user_id', table_name='chat_saved_word')
    op.drop_index('ix_chat_saved_word_session_id', table_name='chat_saved_word')
    op.drop_table('chat_saved_word')

    op.drop_index('ix_chat_turn_session_idx', table_name='chat_turn')
    op.drop_index('ix_chat_turn_session_id', table_name='chat_turn')
    op.drop_table('chat_turn')

    op.drop_index('ix_chat_session_seed_video_id', table_name='chat_session')
    op.drop_index('ix_chat_session_user_id', table_name='chat_session')
    op.drop_table('chat_session')
