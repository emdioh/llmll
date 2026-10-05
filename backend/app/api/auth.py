"""Optional shared-token access control.

When `LLMLL_ACCESS_TOKEN` is unset everything stays open (local development). When it is set,
every `/api/*` route except `GET /api/health` and the `/api/auth/*` endpoints requires either
`Authorization: Bearer <token>` or a session cookie obtained from `POST /api/auth/login`.
The cookie holds an HMAC of a fixed label keyed with the token, so the raw token never sits in
the cookie and rotating the token invalidates every session.
"""

import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, SecretStr

from app.api.deps import get_now
from app.config import Settings

COOKIE_NAME = "llmll_session"
COOKIE_MAX_AGE = 90 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token(settings: Settings) -> str | None:
    secret: SecretStr | None = settings.access_token
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value or None


def session_value(token: str) -> str:
    return hmac.new(token.encode(), b"llmll-session-v1", hashlib.sha256).hexdigest()


def is_authenticated(request: Request) -> bool:
    token = _token(request.app.state.settings)
    if token is None:
        return True
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(
        supplied.strip().encode(), token.encode()
    ):
        return True
    cookie = request.cookies.get(COOKIE_NAME)
    return cookie is not None and hmac.compare_digest(
        cookie.encode(), session_value(token).encode()
    )


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


class LoginThrottle:
    """In-memory failed-login throttling per client address."""

    def __init__(self) -> None:
        self._failures: dict[str, list[datetime]] = {}
        self._blocked_until: dict[str, datetime] = {}

    def blocked(self, key: str, now: datetime) -> bool:
        until = self._blocked_until.get(key)
        if until is None:
            return False
        if now >= until:
            del self._blocked_until[key]
            self._failures.pop(key, None)
            return False
        return True

    def record_failure(self, key: str, now: datetime) -> None:
        recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW]
        recent.append(now)
        self._failures[key] = recent
        if len(recent) >= MAX_FAILURES:
            self._blocked_until[key] = now + LOCKOUT

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._blocked_until.pop(key, None)


class LoginIn(BaseModel):
    token: str


class AuthStatus(BaseModel):
    auth: Literal["enabled", "disabled"]
    authenticated: bool


def auth_mode(request: Request) -> Literal["enabled", "disabled"]:
    return "enabled" if _token(request.app.state.settings) else "disabled"


@router.get("/status", response_model=AuthStatus, operation_id="getAuthStatus")
def auth_status(request: Request) -> AuthStatus:
    return AuthStatus(auth=auth_mode(request), authenticated=is_authenticated(request))


@router.post("/login", response_model=AuthStatus, operation_id="login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    now: Callable[[], datetime] = Depends(get_now),
) -> AuthStatus:
    settings: Settings = request.app.state.settings
    token = _token(settings)
    if token is None:
        return AuthStatus(auth="disabled", authenticated=True)
    throttle: LoginThrottle = request.app.state.login_throttle
    key = request.client.host if request.client else "unknown"
    when = now()
    if throttle.blocked(key, when):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts, try again later",
        )
    if not hmac.compare_digest(body.token.encode(), token.encode()):
        throttle.record_failure(key, when)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    throttle.reset(key)
    response.set_cookie(
        COOKIE_NAME,
        session_value(token),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure or request.url.scheme == "https",
        path="/",
    )
    return AuthStatus(auth="enabled", authenticated=True)


@router.post("/logout", response_model=AuthStatus, operation_id="logout")
def logout(request: Request, response: Response) -> AuthStatus:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return AuthStatus(auth=auth_mode(request), authenticated=False)
