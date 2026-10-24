import secrets
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sc2scala.errors import SignInError
from sc2scala.server.github_accounts import GitHubAccounts, GitHubUser
from sc2scala.server.models import User
from sc2scala.server.web import (
    DatabaseSession,
    check_csrf,
    is_valid_login,
    local_path_or,
    render,
    sign_in_as,
    sign_out,
    signed_in_user,
)

router = APIRouter()

_STATE_SESSION_KEY = "github_state"
_NEXT_SESSION_KEY = "after_sign_in"
_AFTER_SIGN_IN = "/account"


@router.get("/sign-in", response_class=HTMLResponse)
def sign_in_page(request: Request, session: DatabaseSession, next: str | None = None) -> HTMLResponse:
    return render(
        request,
        "sign_in.html",
        user=signed_in_user(request, session),
        next=local_path_or(next, _AFTER_SIGN_IN),
        github_sign_in=request.app.state.github_accounts is not None,
        dev_sign_in=request.app.state.config.dev_sign_in,
    )


@router.get("/sign-in/github")
def sign_in_with_github(request: Request, next: str | None = None) -> RedirectResponse:
    github_accounts: GitHubAccounts | None = request.app.state.github_accounts
    if github_accounts is None:
        raise HTTPException(status_code=404, detail="This server has no sign-in with GitHub")
    state = secrets.token_urlsafe(32)
    request.session[_STATE_SESSION_KEY] = state
    request.session[_NEXT_SESSION_KEY] = local_path_or(next, _AFTER_SIGN_IN)
    authorize_url = github_accounts.authorize_url(state=state, redirect_uri=_github_callback_url(request))
    return RedirectResponse(authorize_url, status_code=303)


@router.get("/sign-in/github/callback")
def github_callback(request: Request, session: DatabaseSession, code: str = "", state: str = "") -> RedirectResponse:
    github_accounts: GitHubAccounts | None = request.app.state.github_accounts
    expected_state = request.session.pop(_STATE_SESSION_KEY, None)
    after_sign_in = request.session.pop(_NEXT_SESSION_KEY, _AFTER_SIGN_IN)
    if github_accounts is None or expected_state is None or not secrets.compare_digest(expected_state, state):
        raise SignInError("The sign-in with GitHub was not started here, or has expired; start it again")
    account = github_accounts.user_for_code(code, redirect_uri=_github_callback_url(request))
    sign_in_as(request, _user_for_github_account(session, account))
    return RedirectResponse(local_path_or(after_sign_in, _AFTER_SIGN_IN), status_code=303)


@router.post("/sign-in/dev")
def dev_sign_in(
    request: Request,
    session: DatabaseSession,
    login: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    next: Annotated[str, Form()] = _AFTER_SIGN_IN,
) -> RedirectResponse:
    if not request.app.state.config.dev_sign_in:
        raise HTTPException(status_code=404, detail="This server has no dev sign-in")
    check_csrf(request, csrf_token)
    login = login.strip()
    if not is_valid_login(login):
        raise SignInError(f"{login!r} is no name GitHub could give: letters, digits and '-', 39 at most")
    user = user_named(session, login)
    if user is not None and user.github_id is not None:
        raise SignInError(f"{login} signs in with GitHub")
    if user is None:
        user = User(login=login)
        session.add(user)
        session.commit()
    sign_in_as(request, user)
    return RedirectResponse(local_path_or(next, _AFTER_SIGN_IN), status_code=303)


@router.post("/sign-out")
def sign_out_route(request: Request, csrf_token: Annotated[str, Form()]) -> RedirectResponse:
    check_csrf(request, csrf_token)
    sign_out(request)
    return RedirectResponse("/", status_code=303)


def _github_callback_url(request: Request) -> str:
    return f"{request.app.state.config.public_url}/sign-in/github/callback"


def user_named(session: Session, login: str) -> User | None:
    """The user whose login is `login`, in any case, as GitHub compares them."""
    return session.scalar(select(User).where(func.lower(User.login) == login.lower()))


def _user_for_github_account(session: Session, account: GitHubUser) -> User:
    """The user of `account`, made at its first sign-in, and renamed if its GitHub login changed.

    A GitHub login is given anew once its owner renames, so another GitHub user still named `account.login` here is
    renamed `<login>-<their GitHub id>`, until they sign in again. Raises `SignInError` if a dev user holds the login.
    """
    user = session.scalar(select(User).where(User.github_id == account.id))
    holder_of_login = user_named(session, account.login)
    if holder_of_login is not None and (user is None or holder_of_login.id != user.id):
        if holder_of_login.github_id is None:
            raise SignInError(f"Another account here is named {account.login}")
        holder_of_login.login = f"{holder_of_login.login}-{holder_of_login.github_id}"
        session.flush()
    if user is None:
        user = User(login=account.login, github_id=account.id)
        session.add(user)
    else:
        user.login = account.login
    session.commit()
    return user
