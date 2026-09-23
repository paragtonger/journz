import hashlib
import secrets
from datetime import timedelta
from hmac import compare_digest

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from sqlmodel import Session, select

from .models import Session as UserSession
from .models import User, utc_now

password_hash = PasswordHash.recommended()
SESSION_COOKIE = "journz_session"
CSRF_COOKIE = "journz_csrf"
SESSION_LENGTH = timedelta(days=30)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_csrf(request: Request, submitted_token: str | None = None) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    request_token = submitted_token or request.headers.get("X-CSRF-Token")
    if (
        not cookie_token
        or not request_token
        or not compare_digest(cookie_token, request_token)
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def get_current_user(request: Request, session: Session) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    stored = session.exec(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.expires_at > utc_now(),
        )
    ).first()
    if not stored:
        return None
    stored.last_used_at = utc_now()
    session.add(stored)
    session.commit()
    return session.get(User, stored.user_id)


def create_session(user: User, session: Session) -> str:
    token = secrets.token_urlsafe(32)
    if user.id is None:
        raise ValueError("Cannot create a session for a user without an ID")
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=utc_now() + SESSION_LENGTH,
        )
    )
    session.commit()
    return token
