import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledgedesk.persistence.models import LoginSession, User
from knowledgedesk.settings import Settings

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise ValueError("password must contain between 12 and 256 characters")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError, InvalidHashError, VerificationError:
        return False


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def create_session(
    session: Session, user: User, settings: Settings
) -> tuple[str, str, LoginSession]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    record = LoginSession(
        user_id=user.id,
        token_hash=_digest(token),
        csrf_hash=_digest(csrf),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_seconds),
    )
    session.add(record)
    session.flush()
    return token, csrf, record


def lookup_session(session: Session, token: str | None) -> tuple[User, LoginSession] | None:
    if not token:
        return None
    record = session.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == _digest(token),
            LoginSession.expires_at > datetime.now(UTC),
        )
    )
    if record is None:
        return None
    user = session.get(User, record.user_id)
    if user is None or user.disabled_at is not None:
        return None
    return user, record


def verify_csrf(record: LoginSession, cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    if not hmac.compare_digest(cookie_value, header_value):
        return False
    return hmac.compare_digest(record.csrf_hash, _digest(header_value))


def membership_role(session: Session, user_id: UUID, workspace_id: UUID) -> str | None:
    from knowledgedesk.persistence.models import Membership

    return session.scalar(
        select(Membership.role).where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
        )
    )
