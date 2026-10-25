from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.race import Race
from sc2scala.server.api_tokens import hash_of_token, new_token
from sc2scala.server.bots_api import bots_of
from sc2scala.server.ladder_runners_api import barred_from
from sc2scala.server.ladders_api import visible_ladders
from sc2scala.server.models import ApiToken, RunnerMachine, User
from sc2scala.server.web import DatabaseSession, check_csrf, render, required_user

router = APIRouter()

_MAX_TOKEN_NAME_LENGTH = 100


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request, session: DatabaseSession, user: Annotated[User, Depends(required_user)]
) -> HTMLResponse:
    return _render_account(request, session, user, new_token=None)


@router.post("/account/tokens", response_class=HTMLResponse)
def create_token(
    request: Request,
    session: DatabaseSession,
    user: Annotated[User, Depends(required_user)],
    name: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
) -> HTMLResponse:
    """Makes a token, shown on the page that answers, and never again."""
    check_csrf(request, csrf_token)
    token = new_token()
    name = name.strip()[:_MAX_TOKEN_NAME_LENGTH] or "unnamed"
    session.add(ApiToken(user_id=user.id, name=name, token_hash=hash_of_token(token)))
    session.commit()
    session.refresh(user)
    return _render_account(request, session, user, new_token=token, new_token_name=name)


@router.post("/account/tokens/{token_id}/delete")
def delete_token(
    request: Request,
    session: DatabaseSession,
    user: Annotated[User, Depends(required_user)],
    token_id: int,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    check_csrf(request, csrf_token)
    api_token = session.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    if api_token is not None:
        session.delete(api_token)
        session.commit()
    return RedirectResponse("/account", status_code=303)


def _render_account(request: Request, session: Session, user: User, **context: Any) -> HTMLResponse:
    bots = bots_of(session, user)
    machines = list(
        session.scalars(select(RunnerMachine).where(RunnerMachine.owner_id == user.id).order_by(RunnerMachine.name))
    )
    servable_ladders = visible_ladders(session, user)
    return render(
        request,
        "account.html",
        user=user,
        bots=bots,
        machines=machines,
        barred_from={machine.id: barred_from(session, machine) for machine in machines},
        servable_ladders=servable_ladders,
        races=list(Race),
        **context,
    )
