import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_user_with_new_flag
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import UserResponse
from app.services.account_deletion import delete_local_user_data


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/auth/me", response_model=UserResponse)
def me(current_user_and_flag: tuple[User, bool] = Depends(get_current_user_with_new_flag)):
    """Return the local profile mapped to the verified Supabase Auth user.

    is_new_user is True only on the request that just created the local row —
    the frontend uses it to route a fresh Supabase sign-in (including the
    Google redirect flow, which discards any page-local navigation state)
    through onboarding instead of straight into the app.
    """
    current_user, is_new = current_user_and_flag
    return UserResponse.model_validate(current_user, from_attributes=True).model_copy(update={"is_new_user": is_new})


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
    except HTTPError as exc:
        # A previous attempt can have completed Auth deletion just before its
        # response was lost. Treat that retry as complete, not as an error.
        if exc.code == status.HTTP_404_NOT_FOUND:
            return
        logger.warning("Supabase Admin account deletion failed with status %s", exc.code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not delete your sign-in account. Nothing was deleted; please try again.",
        ) from exc
    except (URLError, OSError) as exc:
        logger.warning("Supabase Admin account deletion could not be reached: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the sign-in service. Nothing was deleted; please try again.",
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
    try:
        delete_local_user_data(db, current_user)

        # Validate all local deletes before touching Supabase. If Auth deletion
        # fails, rollback restores the complete local account and a retry is safe.
        db.flush()
        _delete_supabase_user(supabase_user_id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Local account deletion failed for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete account data. Nothing was deleted; please try again.",
        ) from exc
