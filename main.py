# Deploy trigger: 2026-05-28
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.database import engine, Base
from app.models.user import User  # noqa: F401 — registers model with Base
from app.models.video import TrackedVideo  # noqa: F401
from app.models.user_video_watch import UserVideoWatch  # noqa: F401
from app.models.user_flashcard_progress import UserFlashcardProgress  # noqa: F401
from app.models.user_review_history import UserReviewHistory  # noqa: F401
from app.models.deck_settings import DeckSettings  # noqa: F401
from app.models.user_vocabulary_list import UserVocabularyList  # noqa: F401
from app.models.user_vocabulary_word import UserVocabularyWord  # noqa: F401
from app.models.user_vocabulary_settings import UserVocabularySettings  # noqa: F401
from app.models.user_mined_word import UserMinedWord  # noqa: F401
from app.models.user_anki_progress import UserAnkiProgress  # noqa: F401
from app.models.image_cache import ImageCache  # noqa: F401
from app.models.community_group import CommunityGroup  # noqa: F401
from app.models.community_membership import CommunityMembership  # noqa: F401
from app.models.community_vocab_list import CommunityVocabList  # noqa: F401
from app.models.community_vocab_word import CommunityVocabWord  # noqa: F401
from app.api.routes import health
from app.api.routes.auth import router as auth_router
from app.api.routes.videos import router as videos_router
from app.api.routes.subtitles import router as subtitles_router
from app.api.routes.vocabulary import router as vocabulary_router
from app.api.routes.flashcards import router as flashcards_router
from app.api.routes.lookup import router as lookup_router
from app.api.routes.netflix import router as netflix_router
from app.api.routes.fsrs import router as fsrs_router
from app.api.routes.decks import router as decks_router
from app.api.routes.user_vocab import router as user_vocab_router
from app.api.routes.anki import router as anki_router
from app.api.routes.chat import router as chat_router
from app.api.routes.chat_voice import router as chat_voice_router
from app.api.routes.converse_v2 import router as converse_v2_router
from app.api.routes.community import router as community_router


Base.metadata.create_all(bind=engine)

# SQLite migration: add new columns if they don't exist yet
if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import text
    with engine.connect() as conn:
        # Migrations for tracked_videos table
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(tracked_videos)")).fetchall()]
        migrations = [
            ("has_ukrainian",    "BOOLEAN"),
            ("has_serbian",      "BOOLEAN"),
            ("has_bulgarian",    "BOOLEAN"),
            ("duration_seconds", "FLOAT"),
            ("season",           "INTEGER"),
            ("episode",          "INTEGER"),
            ("episode_title",    "TEXT"),
        ]
        for col, coltype in migrations:
            if col not in cols:
                conn.execute(text(f"ALTER TABLE tracked_videos ADD COLUMN {col} {coltype}"))

        # Migrations for user_flashcard_progress table
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(user_flashcard_progress)")).fetchall()]
        if "video_id" not in cols:
            conn.execute(text("ALTER TABLE user_flashcard_progress ADD COLUMN video_id TEXT"))

        # Migrations for users table (password reset)
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
        if "reset_token" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN reset_token TEXT"))
        if "reset_token_expires" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN reset_token_expires DATETIME"))

        # Migrations for user_video_watches table (actual watch time)
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(user_video_watches)")).fetchall()]
        if "watch_time_seconds" not in cols:
            conn.execute(text("ALTER TABLE user_video_watches ADD COLUMN watch_time_seconds INTEGER DEFAULT 0"))

        conn.commit()

# PostgreSQL migration: add watch_time_seconds column if it doesn't exist
if settings.DATABASE_URL.startswith("postgres"):
    from sqlalchemy import text
    with engine.connect() as conn:
        # Check if column exists in PostgreSQL
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'user_video_watches' AND column_name = 'watch_time_seconds'
        """))
        if not result.fetchone():
            conn.execute(text("ALTER TABLE user_video_watches ADD COLUMN watch_time_seconds INTEGER DEFAULT 0"))
            conn.commit()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return JSON with proper CORS headers."""
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(videos_router, prefix="/api/videos", tags=["videos"])
app.include_router(subtitles_router, prefix="/api", tags=["subtitles"])
app.include_router(vocabulary_router, prefix="/api", tags=["vocabulary"])
app.include_router(flashcards_router, prefix="/api", tags=["flashcards"])
app.include_router(lookup_router, prefix="/api", tags=["lookup"])
app.include_router(netflix_router, prefix="/api/netflix", tags=["netflix"])
app.include_router(fsrs_router, prefix="/api/fsrs", tags=["fsrs"])
app.include_router(decks_router, prefix="/api/decks", tags=["decks"])
app.include_router(user_vocab_router, prefix="/api/vocab", tags=["user-vocab"])
app.include_router(anki_router, prefix="/api/anki", tags=["anki"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(chat_voice_router, prefix="/api", tags=["chat-voice"])
app.include_router(converse_v2_router, prefix="/api/converse2", tags=["converse2"])
app.include_router(community_router, prefix="/api/community", tags=["community"])


@app.get("/")
async def root():
    return {
        "message": "Deadbird API",
        "version": settings.VERSION,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
