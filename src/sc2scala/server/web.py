"""What the server's routes share: the database session, who is signed in, forms' CSRF tokens and notices, and the
templates."""

import re
import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.errors import UploadRefusedError
from sc2scala.server.api_tokens import hash_of_token
from sc2scala.server.models import ApiToken, RunnerMachine, User, utc_now

_CSRF_SESSION_KEY = "csrf_token"
_NOTICE_SESSION_KEY = "notice"
_LONGEST_NOTICE = 300
"""The most characters a notice keeps, of a message or of a field kept, so that the session's cookie stays small."""
_USER_SESSION_KEY = "user_id"
_LAST_USE_PRECISION = timedelta(minutes=1)
"""How stale a token's time of last use may grow before a request brings it up to date."""
_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


class SignInRequired(Exception):
    """The page needs someone signed in, and nobody is; the server sends them to sign in, then back to `path`."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def database_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


DatabaseSession = Annotated[Session, Depends(database_session)]


def signed_in_user(request: Request, session: DatabaseSession) -> User | None:
    """Whoever is signed in on the browser that asks, or `None`."""
    user_id = request.session.get(_USER_SESSION_KEY)
    return None if user_id is None else session.get(User, user_id)


def required_user(request: Request, user: Annotated[User | None, Depends(signed_in_user)]) -> User:
    """Whoever is signed in; raises `SignInRequired` if nobody is, to come back to the page asked for, or for a form
    sent, to the page it was sent from."""
    if user is None:
        if request.method != "GET":
            raise SignInRequired(local_path_or(urlsplit(request.headers.get("referer", "")).path, "/"))
        query = request.url.query
        raise SignInRequired(f"{request.url.path}?{query}" if query else request.url.path)
    return user


def token_user(request: Request, session: DatabaseSession) -> User:
    """The user whose token the request bears as `Authorization: Bearer <token>`; 401 if it bears none that is valid.

    The token's time of last use is brought up to date, to the minute.
    """
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    api_token = None
    if scheme.lower() == "bearer" and token:
        api_token = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_of_token(token.strip())))
    if api_token is None:
        raise HTTPException(status_code=401, detail="No valid token", headers={"WWW-Authenticate": "Bearer"})
    now = utc_now()
    if api_token.last_used_at is None or now - api_token.last_used_at > _LAST_USE_PRECISION:
        api_token.last_used_at = now
        session.commit()
    return api_token.user


def runner_machine(request: Request, session: DatabaseSession) -> RunnerMachine:
    """The runner machine whose token the request bears as `Authorization: Bearer <token>`; 401 if none that is valid.

    The machine's time of last contact is brought up to date, to the minute.
    """
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    machine = None
    if scheme.lower() == "bearer" and token:
        machine = session.scalar(select(RunnerMachine).where(RunnerMachine.token_hash == hash_of_token(token.strip())))
    if machine is None:
        raise HTTPException(status_code=401, detail="No valid runner token", headers={"WWW-Authenticate": "Bearer"})
    now = utc_now()
    if machine.last_seen_at is None or now - machine.last_seen_at > _LAST_USE_PRECISION:
        machine.last_seen_at = now
        session.commit()
    return machine


def sign_in_as(request: Request, user: User) -> None:
    """Signs `user` in on the browser that asks, in a session of its own."""
    request.session.clear()
    request.session[_USER_SESSION_KEY] = user.id


def sign_out(request: Request) -> None:
    request.session.clear()


def is_valid_login(login: str) -> bool:
    """Whether `login` is a name GitHub could give: letters, digits and `-`, 39 at most, not beginning with `-`."""
    return _LOGIN_PATTERN.fullmatch(login) is not None


def local_path_or(path: str | None, default: str) -> str:
    """`path` if it is a path on this server, else `default`."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return default


def csrf_token(request: Request) -> str:
    """The token the session's forms carry, made the first time it is asked for."""
    token = request.session.get(_CSRF_SESSION_KEY)
    if token is None:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def check_csrf(request: Request, submitted: str) -> None:
    """403 unless `submitted` is the session's form token."""
    expected = request.session.get(_CSRF_SESSION_KEY)
    if expected is None or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="The form has expired; load the page again")


def render(
    request: Request, template: str, *, user: User | None, status_code: int = 200, **context: Any
) -> HTMLResponse:
    """The page `template`, given who is signed in, the session's form token, the notice a form left, and `context`."""
    return request.app.state.templates.TemplateResponse(
        request,
        template,
        {
            "user": user,
            "csrf_token": csrf_token(request),
            "notice": _notice_for(request),
            **context,
        },
        status_code=status_code,
    )


def redirect_with_notice(
    request: Request, path: str, message: str, *, refused: bool = False, kept: dict[str, str] | None = None
) -> RedirectResponse:
    """Sends the browser to `path`, whose page then shows `message` once: what a form did, or why it was `refused`,
    with what it held that the page fills in again, `kept`; each cut to 300 characters."""
    request.session[_NOTICE_SESSION_KEY] = {
        "path": path,
        "message": _shortened(message),
        "refused": refused,
        "kept": {name: _shortened(value) for name, value in (kept or {}).items()},
    }
    return RedirectResponse(path, status_code=303)


def refusal_reason(error: HTTPException | UploadRefusedError) -> str:
    """Why a form's action was refused, in words its page shows."""
    return str(error.detail) if isinstance(error, HTTPException) else str(error)


def _notice_for(request: Request) -> dict[str, Any] | None:
    """The notice a form left for this page, which it takes, so that no other page, in another tab say, shows it."""
    notice = request.session.get(_NOTICE_SESSION_KEY)
    if notice is None or notice.get("path") != request.url.path:
        return None
    return request.session.pop(_NOTICE_SESSION_KEY)


def _shortened(text: str) -> str:
    return text if len(text) <= _LONGEST_NOTICE else f"{text[: _LONGEST_NOTICE - 1]}…"
