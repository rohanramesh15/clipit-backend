import time
import uuid
from threading import Lock
from typing import Optional
from urllib.request import urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.models.user import User
from app.services.account_deletion import delete_local_user_data

bearer_scheme = HTTPBearer()
bearer_scheme_optional = HTTPBearer(auto_error=False)

_JWKS_TTL_SECONDS = 600
_jwks: dict | None = None
_jwks_fetched_at = 0.0
_jwks_lock = Lock()


def _get_jwks() -> dict:
    """Fetch Supabase's public signing keys and retain them for ten minutes."""
    global _jwks, _jwks_fetched_at
    if not settings.SUPABASE_URL:
        raise JWTError("SUPABASE_URL is not configured")

    now = time.monotonic()
    if _jwks and now - _jwks_fetched_at < _JWKS_TTL_SECONDS:
        return _jwks

    with _jwks_lock:
        now = time.monotonic()
        if _jwks and now - _jwks_fetched_at < _JWKS_TTL_SECONDS:
            return _jwks
        jwks_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        with urlopen(jwks_url, timeout=10) as response:
            _jwks = __import__("json").load(response)
        _jwks_fetched_at = now
        return _jwks


def verify_supabase_token(token: str) -> dict:
    """Verify an ES256 Supabase access token against the project's JWKS."""
    try:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        key = next(key for key in _get_jwks().get("keys", []) if key.get("kid") == key_id)
        issuer = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"
        return jwt.decode(
            token,
            key,
            algorithms=["ES256"],
            audience="authenticated",
            issuer=issuer,
        )
    except (JWTError, StopIteration, ValueError, OSError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user_from_token(token: str, db: Session, intent: str | None = None) -> tuple[User, bool]:
    """Return the app user mapped to a verified Supabase Auth subject.

    The first authenticated API request creates the local row, preserving the
    integer primary key expected by all existing application tables. The
    second element of the returned tuple is True only when this call is the
    one that created (or first-linked) the row.

    ``intent`` optionally enforces which flow the caller claims to be
    (Google OAuth always succeeds and silently provisions an Auth identity
    regardless of whether the user clicked "Sign in" or "Sign up", so that
    distinction has to be enforced here instead): "signin" rejects a result
    that would create a brand-new account, and "signup" rejects a result that
    resolves to an account that already existed.
    """
    payload = verify_supabase_token(token)
    try:
        supabase_user_id = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    is_new = False
    user = db.query(User).filter(User.supabase_user_id == supabase_user_id).first()
    if user is None:
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no email claim")
        metadata = payload.get("user_metadata") or {}

        # An account with this email may already exist from before Supabase
        # Auth (the old email/password or Google flow). A row with no
        # Supabase ID is a genuine legacy profile and should be linked. A row
        # with a *different* Supabase ID is a newly created account reusing an
        # email after deletion; retaining it would resurrect old learning data
        # and skip onboarding, so replace it with a clean profile instead.
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            if existing.supabase_user_id is not None and existing.supabase_user_id != supabase_user_id:
                delete_local_user_data(db, existing)
                db.flush()
                is_new = True
                user = User(
                    email=email,
                    full_name=metadata.get("full_name") or metadata.get("name"),
                    profile_picture=metadata.get("avatar_url") or metadata.get("picture"),
                    supabase_user_id=supabase_user_id,
                )
                db.add(user)
            else:
                existing.supabase_user_id = supabase_user_id
                existing.full_name = existing.full_name or metadata.get("full_name") or metadata.get("name")
                existing.profile_picture = existing.profile_picture or metadata.get("avatar_url") or metadata.get("picture")
                user = existing
            db.commit()
            db.refresh(user)
        else:
            is_new = True
            user = User(
                email=email,
                full_name=metadata.get("full_name") or metadata.get("name"),
                profile_picture=metadata.get("avatar_url") or metadata.get("picture"),
                supabase_user_id=supabase_user_id,
            )
            db.add(user)
            try:
                db.commit()
            except Exception:
                db.rollback()
                # Lost a race with a concurrent request for the same subject
                # or email — either is now linked, so re-fetch by whichever
                # matches. Still the same signup event, so is_new stays True.
                user = (
                    db.query(User)
                    .filter((User.supabase_user_id == supabase_user_id) | (User.email == email))
                    .first()
                )
                if user is None:
                    raise
                if user.supabase_user_id != supabase_user_id:
                    if user.supabase_user_id is not None:
                        # The concurrent winner found an old account by email.
                        # Preserve the same fresh-account rule as the normal
                        # path instead of reattaching it to stale data.
                        delete_local_user_data(db, user)
                        db.flush()
                        user = User(
                            email=email,
                            full_name=metadata.get("full_name") or metadata.get("name"),
                            profile_picture=metadata.get("avatar_url") or metadata.get("picture"),
                            supabase_user_id=supabase_user_id,
                        )
                        db.add(user)
                    else:
                        user.supabase_user_id = supabase_user_id
                    db.commit()
                    db.refresh(user)
    if intent == "signin" and is_new:
        # Google's redirect already provisioned the Auth identity before we
        # ever saw it; leave that alone and just refuse to create a local
        # profile for an account that doesn't exist yet.
        delete_local_user_data(db, user)
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_account")
    if intent == "signup" and not is_new:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="account_exists")

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user, is_new


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    user, _is_new = get_current_user_from_token(credentials.credentials, db)
    return user


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Get current user if authenticated, otherwise return None."""
    if credentials is None:
        return None

    try:
        user, _is_new = get_current_user_from_token(credentials.credentials, db)
        return user
    except HTTPException:
        return None


def get_current_user_with_new_flag(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    intent: str | None = None,
) -> tuple[User, bool]:
    """Same as get_current_user, but also reports whether this call is what
    created the local row — used by /auth/me to tell the frontend whether to
    route a Supabase-redirect sign-in through onboarding.

    ``intent`` is the optional ``?intent=signin|signup`` query param a fresh
    Google-redirect request carries; see get_current_user_from_token."""
    return get_current_user_from_token(credentials.credentials, db, intent)
