"""Lock down application tables exposed through Supabase PostgREST.

Revision ID: 4b8f2c9d7e10
Revises: f1a91d3b6e12
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "4b8f2c9d7e10"
down_revision: Union[str, None] = "f1a91d3b6e12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ClipIt accesses its data through the authenticated FastAPI backend, not
# directly from a browser via Supabase PostgREST. Therefore client roles need
# no grants or RLS policies on these application tables. The database owner /
# backend connection remains able to operate normally.
PUBLIC_TABLES = (
    "alembic_version",
    "users",
    "tracked_videos",
    "user_vocabulary_settings",
    "user_vocabulary_lists",
    "user_vocabulary_words",
    "user_flashcard_progress",
    "user_anki_progress",
    "user_review_history",
    "user_mined_words",
    "user_video_watches",
    "user_language_profile",
    "deck_settings",
    "community_groups",
    "community_memberships",
    "community_vocab_lists",
    "community_vocab_words",
    "chat_session",
    "chat_turn",
    "chat_saved_word",
    "chat_memory_fact",
    "cv2_profile",
    "cv2_session",
    "cv2_turn",
    "cv2_feedback",
    "subtitle_embedding",
    "image_cache",
)


def upgrade() -> None:
    for table in PUBLIC_TABLES:
        op.execute(f'ALTER TABLE IF EXISTS public."{table}" ENABLE ROW LEVEL SECURITY')
        # Local Docker Postgres does not create Supabase's client roles. Make
        # the migration portable while still revoking both roles in Supabase.
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON TABLE public.\"{table}\" FROM anon;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    REVOKE ALL ON TABLE public.\"{table}\" FROM authenticated;
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    # Re-enabling public access must be an explicit, reviewed operation. A
    # downgrade only removes RLS; it intentionally does not restore grants.
    for table in PUBLIC_TABLES:
        op.execute(f'ALTER TABLE IF EXISTS public."{table}" DISABLE ROW LEVEL SECURITY')
