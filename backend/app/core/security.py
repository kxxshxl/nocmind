from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AuditEvent, User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 210_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload: dict[str, Any] = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = str(payload["sub"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


ROLES = ("VIEWER", "OPERATOR", "ADMIN")
OPERATORS = ("OPERATOR", "ADMIN")


def audit(db: Session, actor_id: str, event: str, incident_id: str | None = None, **details: Any) -> None:
    """Append an audit record. Commits are left to the caller's transaction where one is open."""
    from app.core.telemetry import correlation_id
    if correlation_id.get():
        details.setdefault("correlation_id", correlation_id.get())
    db.add(AuditEvent(actor_id=actor_id, incident_id=incident_id, action=event, details_json=json.dumps(details, default=str)))


class LoginThrottle:
    """In-process lockout after repeated failed logins (per username). Single-instance only."""

    def __init__(self):
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def locked(self, username: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(username, []) if now - t < settings.login_lockout_seconds]
            self._failures[username] = recent
            return len(recent) >= settings.login_max_failures

    def fail(self, username: str) -> None:
        with self._lock:
            self._failures.setdefault(username, []).append(time.monotonic())

    def reset(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)


login_throttle = LoginThrottle()


def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user
    return dependency
