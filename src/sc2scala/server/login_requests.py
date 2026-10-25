"""A command signing in through the browser: its request, and the page where a user confirms it.

The command makes its token itself and sends its hash, with a name for itself; the server answers with a short code and
the address of the page that confirms it. Once someone signed in there confirms the code, the hash is a token of theirs,
and the command, which asks `/api/me` with its token meanwhile, finds itself signed in. The token never crosses the
network before it is someone's, and the server never holds it.

A code is for the person at the terminal to compare, not a secret: whoever confirms one lets the command that showed it
act for them, so the page asks them to compare it, and says which command asks, from where.
"""

import re
import secrets
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sc2scala.server.bots_api import commit_or_conflict
from sc2scala.server.models import ApiToken, LoginRequest, User, utc_now
from sc2scala.server.web import DatabaseSession, check_csrf, redirect_with_notice, render, required_user

router = APIRouter()

LOGIN_REQUEST_LIFETIME = timedelta(minutes=10)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Letters and digits no one mistakes for one another: no I, O, 0 or 1."""
_CODE_HALF_LENGTH = 4
_MOST_WAITING = 1000
"""How many requests may wait at once, so that requests nobody confirms cannot fill the database."""
_MOST_WAITING_FROM_ONE_ADDRESS = 5
"""How many may wait from one address, so that no one address can keep the others from signing in."""
_NAME_PATTERN = re.compile(r"[A-Za-z0-9 ._()-]{1,60}")
"""What a command may call itself: plain words, as `sc2scala on <host>`, too short to pass for the server speaking."""
_TOKEN_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_CONFIRM_PAGE = "/login"

SignedInUser = Annotated[User, Depends(required_user)]


class LoginRequestBody(BaseModel):
    token_hash: str
    """The SHA-256, in hex, of the token the command made."""
    name: str = "sc2scala"
    """What the command calls itself, which names its token, such as "sc2scala on my-laptop"."""


@router.post("/api/login-requests")
def request_login(request: Request, session: DatabaseSession, body: LoginRequestBody) -> dict[str, Any]:
    """Records a command's request to sign in with the token whose hash it sends, for anyone; answers with the code to
    confirm, the page to confirm it on, and when the request expires.

    422 for a hash that is no SHA-256, 409 for one the server knows already, and 503 while too many requests wait.
    """
    if not _TOKEN_HASH_PATTERN.fullmatch(body.token_hash):
        raise HTTPException(422, "token_hash is the SHA-256 of the token, in lower-case hex")
    name = body.name.strip()
    if not _NAME_PATTERN.fullmatch(name):
        raise HTTPException(422, "name is letters, digits, spaces and . _ ( ) -, 60 at most")
    now = utc_now()
    address = request.client.host if request.client else None
    session.execute(delete(LoginRequest).where(LoginRequest.expires_at < now))
    if (session.scalar(select(func.count()).select_from(LoginRequest)) or 0) >= _MOST_WAITING:
        raise HTTPException(503, "Too many sign-ins are waiting to be confirmed; try again in a few minutes")
    from_address = select(func.count()).select_from(LoginRequest).where(LoginRequest.from_address == address)
    if address is not None and (session.scalar(from_address) or 0) >= _MOST_WAITING_FROM_ONE_ADDRESS:
        raise HTTPException(
            429, "Too many sign-ins wait from this address; confirm or refuse them, or wait ten minutes"
        )
    known = select(ApiToken.id).where(ApiToken.token_hash == body.token_hash)
    if session.scalar(known) is not None or _waiting_by_hash(session, body.token_hash) is not None:
        raise HTTPException(409, "That token is known already; make another")
    login = LoginRequest(
        code=_new_code(session),
        token_hash=body.token_hash,
        name=name,
        from_address=address,
        requested_at=now,
        expires_at=now + LOGIN_REQUEST_LIFETIME,
    )
    session.add(login)
    commit_or_conflict(session, "Another sign-in took that token or code at the same moment; try again")
    public_url = request.app.state.config.public_url
    return {
        "code": login.code,
        "confirm_url": f"{public_url}{_CONFIRM_PAGE}?code={login.code}",
        "expires_at": login.expires_at.isoformat(),
    }


@router.get("/api/login-requests/{code}")
def waiting_request(session: DatabaseSession, code: str) -> dict[str, str]:
    """Whether a request still waits with `code`, for its command to tell a refused or expired one; 404 if not."""
    login = waiting_login(session, code)
    if login is None:
        raise HTTPException(404, f"No sign-in waits with the code {normalized_code(code)}")
    return {"code": login.code, "expires_at": login.expires_at.isoformat()}


@router.get(_CONFIRM_PAGE, response_class=HTMLResponse)
def confirm_page(request: Request, session: DatabaseSession, user: SignedInUser, code: str = "") -> HTMLResponse:
    """The page that asks the user to confirm a command's code, or to type the code if the address lacks it."""
    login = waiting_login(session, code) if code else None
    return render(request, "login_confirm.html", user=user, login=login, code=normalized_code(code) if code else "")


@router.post(_CONFIRM_PAGE)
def answer_login(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    code: Annotated[str, Form()] = "",
    answer: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """Signs the command in as the user, its token theirs, if `answer` is "confirm"; else refuses it."""
    check_csrf(request, csrf_token)
    login = waiting_login(session, code)
    if login is None:
        return redirect_with_notice(
            request,
            _CONFIRM_PAGE,
            f"No command waits with the code {normalized_code(code)}: it expired, or was answered already",
            refused=True,
        )
    name = login.name
    session.delete(login)
    if answer != "confirm":
        session.commit()
        return redirect_with_notice(request, "/account", f"Refused the sign-in of {name}")
    session.add(ApiToken(user_id=user.id, name=name, token_hash=login.token_hash))
    commit_or_conflict(session, "That command's token was taken at the same moment; run its sign-in again")
    return redirect_with_notice(
        request, "/account", f"{name} is signed in as you: go back to it. Its token is listed below, to delete"
    )


def waiting_login(session: Session, code: str) -> LoginRequest | None:
    """The request waiting with `code`, in any case and with or without its dash, or `None` if none does."""
    login = session.scalar(select(LoginRequest).where(LoginRequest.code == normalized_code(code)))
    if login is None or login.expires_at < utc_now():
        return None
    return login


def normalized_code(code: str) -> str:
    """`code` as the server writes codes, `ABCD-2345`: upper case, its dash put back if typed without."""
    letters = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    if len(letters) == 2 * _CODE_HALF_LENGTH:
        return f"{letters[:_CODE_HALF_LENGTH]}-{letters[_CODE_HALF_LENGTH:]}"
    return code.strip().upper()


def _new_code(session: Session) -> str:
    while True:
        halves = ("".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_HALF_LENGTH)) for _ in range(2))
        code = "-".join(halves)
        if session.scalar(select(LoginRequest.id).where(LoginRequest.code == code)) is None:
            return code


def _waiting_by_hash(session: Session, token_hash: str) -> LoginRequest | None:
    return session.scalar(select(LoginRequest).where(LoginRequest.token_hash == token_hash))
