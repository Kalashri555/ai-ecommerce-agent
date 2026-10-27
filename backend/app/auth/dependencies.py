"""Authentication for the HTTP API (DEMO implementation).

The identity provider is isolated behind `get_current_user`, so it can later
be replaced (OAuth/OIDC, sessions, ...) without touching routes or the agent.
NOT production-ready: no refresh tokens, no revocation, no account lockout.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.signing import InvalidSignature, sign, verify
from app.config import get_settings
from app.database.connection import get_db
from app.database.models import User

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str


def issue_token(user: User) -> str:
    s = get_settings()
    return sign({"sub": user.id, "usr": user.username, "typ": "login"}, s.app_secret_key, s.token_ttl_minutes * 60)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": "Please log in."},
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        body = verify(creds.credentials, get_settings().app_secret_key)
    except InvalidSignature:
        raise unauthorized
    if body.get("typ") != "login":
        raise unauthorized
    user = db.get(User, int(body["sub"]))
    if user is None:
        raise unauthorized
    return CurrentUser(id=user.id, username=user.username, display_name=user.display_name)
