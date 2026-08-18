from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.deck_settings import DeckSettings
from app.models.user import User
from app.models.user_anki_progress import UserAnkiProgress
from app.models.user_flashcard_progress import UserFlashcardProgress
from app.models.user_mined_word import UserMinedWord
from app.models.user_review_history import UserReviewHistory
from app.models.user_video_watch import UserVideoWatch
from app.models.user_vocabulary_list import UserVocabularyList
from app.models.user_vocabulary_settings import UserVocabularySettings
from app.schemas.user import UserResponse


router = APIRouter()


@router.get("/auth/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    """Return the local profile mapped to the verified Supabase Auth user."""
    return current_user


def _delete_supabase_user(supabase_user_id: str) -> None:
    """Delete the source Auth account with the backend-only service-role key."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase account deletion is not configured",
        )
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/users/{supabase_user_id}"
    request = Request(
        url,
        method="DELETE",
        headers={
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        },
    )
    try:
        with urlopen(request, timeout=15):
            pass
    except (HTTPError, URLError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not delete the Supabase Auth account",
        ) from exc


@router.delete("/auth/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently delete the local profile and its Supabase Auth account."""
    if current_user.supabase_user_id is None:
        raise HTTPException(status_code=409, detail="User is not linked to Supabase Auth")

    user_id = current_user.id
    supabase_user_id = str(current_user.supabase_user_id)
    # Explicitly clear user-owned rows. Some FKs use ON DELETE CASCADE and some
    # do not, so deleting all known direct dependents keeps this resilient.
    db.query(UserReviewHistory).filter(UserReviewHistory.user_id == user_id).delete(synchronize_session=False)
    db.query(UserFlashcardProgress).filter(UserFlashcardProgress.user_id == user_id).delete(synchronize_session=False)
    db.query(UserVideoWatch).filter(UserVideoWatch.user_id == user_id).delete(synchronize_session=False)
    db.query(UserAnkiProgress).filter(UserAnkiProgress.user_id == user_id).delete(synchronize_session=False)
    db.query(UserMinedWord).filter(UserMinedWord.user_id == user_id).delete(synchronize_session=False)
    db.query(DeckSettings).filter(DeckSettings.user_id == user_id).delete(synchronize_session=False)
    db.query(UserVocabularySettings).filter(UserVocabularySettings.user_id == user_id).delete(synchronize_session=False)
    db.query(UserVocabularyList).filter(UserVocabularyList.user_id == user_id).delete(synchronize_session=False)
    db.delete(current_user)
    db.commit()

    _delete_supabase_user(supabase_user_id)
