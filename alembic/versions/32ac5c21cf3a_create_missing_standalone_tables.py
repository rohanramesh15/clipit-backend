"""create missing standalone tables

Revision ID: 32ac5c21cf3a
Revises: 7c30a6d7cd84
Create Date: 2026-08-18 19:35:49.731986

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32ac5c21cf3a'
down_revision: Union[str, None] = '7c30a6d7cd84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cv2_profile',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_key', sa.String(), nullable=True),
    sa.Column('level', sa.String(), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('english_support', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cv2_profile_user_key'), 'cv2_profile', ['user_key'], unique=True)

    op.create_table('cv2_session',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_key', sa.String(), nullable=True),
    sa.Column('seed_type', sa.String(), nullable=True),
    sa.Column('seed_label', sa.String(), nullable=True),
    sa.Column('seed_video_id', sa.String(), nullable=True),
    sa.Column('level', sa.String(), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('english_support', sa.String(), nullable=True),
    sa.Column('due_words_json', sa.Text(), nullable=True),
    sa.Column('difficulty_nudge', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cv2_session_user_key'), 'cv2_session', ['user_key'], unique=False)

    op.create_table('cv2_turn',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=True),
    sa.Column('idx', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(), nullable=True),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('meta_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cv2_turn_session_id'), 'cv2_turn', ['session_id'], unique=False)

    op.create_table('cv2_feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=True),
    sa.Column('turn_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cv2_feedback_session_id'), 'cv2_feedback', ['session_id'], unique=False)

    op.create_table('image_cache',
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('image_data', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_index(op.f('ix_image_cache_key'), 'image_cache', ['key'], unique=False)

    op.create_table('deck_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('video_id', sa.String(length=20), nullable=False),
    sa.Column('custom_name', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'video_id', name='uq_deck_settings_user_video')
    )
    op.create_index(op.f('ix_deck_settings_id'), 'deck_settings', ['id'], unique=False)
    op.create_index(op.f('ix_deck_settings_user_id'), 'deck_settings', ['user_id'], unique=False)
    op.create_index(op.f('ix_deck_settings_video_id'), 'deck_settings', ['video_id'], unique=False)

    op.create_table('user_anki_progress',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('word', sa.String(length=100), nullable=False),
    sa.Column('language', sa.String(length=10), nullable=False, server_default='ko'),
    sa.Column('deck_name', sa.String(length=100), nullable=True),
    sa.Column('due', sa.DateTime(), nullable=True),
    sa.Column('stability', sa.Float(), nullable=False, server_default='0'),
    sa.Column('difficulty', sa.Float(), nullable=False, server_default='0'),
    sa.Column('elapsed_days', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('scheduled_days', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('reps', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('lapses', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('state', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('last_review', sa.DateTime(), nullable=True),
    sa.Column('applied_to_flashcard', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'word', 'language', name='uq_user_anki_word_lang')
    )
    op.create_index(op.f('ix_user_anki_progress_id'), 'user_anki_progress', ['id'], unique=False)
    op.create_index(op.f('ix_user_anki_progress_user_id'), 'user_anki_progress', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_anki_progress_word'), 'user_anki_progress', ['word'], unique=False)

    op.create_table('user_review_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('word', sa.String(), nullable=False),
    sa.Column('language', sa.String(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('clip_duration', sa.Float(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_review_history_id'), 'user_review_history', ['id'], unique=False)
    op.create_index(op.f('ix_user_review_history_user_id'), 'user_review_history', ['user_id'], unique=False)

    op.create_table('user_mined_words',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('video_id', sa.String(length=100), nullable=False),
    sa.Column('word', sa.String(length=100), nullable=False),
    sa.Column('language', sa.String(length=10), nullable=False, server_default='ko'),
    sa.Column('timestamp', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'video_id', 'word', 'language', name='uq_user_video_word_lang')
    )
    op.create_index(op.f('ix_user_mined_words_id'), 'user_mined_words', ['id'], unique=False)
    op.create_index(op.f('ix_user_mined_words_user_id'), 'user_mined_words', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_mined_words_video_id'), 'user_mined_words', ['video_id'], unique=False)
    op.create_index(op.f('ix_user_mined_words_word'), 'user_mined_words', ['word'], unique=False)

    op.create_table('user_video_watches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('video_id', sa.String(), nullable=False),
    sa.Column('watched_at', sa.Float(), nullable=False),
    sa.Column('watch_time_seconds', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'video_id')
    )
    op.create_index(op.f('ix_user_video_watches_id'), 'user_video_watches', ['id'], unique=False)
    op.create_index(op.f('ix_user_video_watches_user_id'), 'user_video_watches', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_video_watches_video_id'), 'user_video_watches', ['video_id'], unique=False)


def downgrade() -> None:
    op.drop_table('user_video_watches')
    op.drop_table('user_mined_words')
    op.drop_table('user_review_history')
    op.drop_table('user_anki_progress')
    op.drop_table('deck_settings')
    op.drop_table('image_cache')
    op.drop_table('cv2_feedback')
    op.drop_table('cv2_turn')
    op.drop_table('cv2_session')
    op.drop_table('cv2_profile')
