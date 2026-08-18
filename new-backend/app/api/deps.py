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


def get_current_user_from_token(token: str, db: Session) -> User:
    """Return the app user mapped to a verified Supabase Auth subject.

    The first authenticated API request creates the local row, preserving the
    integer primary key expected by all existing application tables.
    """
    payload = verify_supabase_token(token)
    try:
        supabase_user_id = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    user = db.query(User).filter(User.supabase_user_id == supabase_user_id).first()
    if user is None:
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no email claim")
        metadata = payload.get("user_metadata") or {}

        # An account with this email may already exist from before Supabase
        # Auth (the old email/password or Google flow) — link it instead of
        # inserting a duplicate, which would violate the email uniqueness
        # constraint.
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            existing.supabase_user_id = supabase_user_id
            existing.full_name = existing.full_name or metadata.get("full_name") or metadata.get("name")
            existing.profile_picture = existing.profile_picture or metadata.get("avatar_url") or metadata.get("picture")
            db.commit()
            db.refresh(existing)
            user = existing
        else:
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
                # matches.
                user = (
                    db.query(User)
                    .filter((User.supabase_user_id == supabase_user_id) | (User.email == email))
                    .first()
                )
                if user is None:
                    raise
                if user.supabase_user_id != supabase_user_id:
                    user.supabase_user_id = supabase_user_id
                    db.commit()
                    db.refresh(user)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    return get_current_user_from_token(credentials.credentials, db)


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Get current user if authenticated, otherwise return None."""
    if credentials is None:
        return None

    try:
        return get_current_user_from_token(credentials.credentials, db)
    except HTTPException:
        return None
