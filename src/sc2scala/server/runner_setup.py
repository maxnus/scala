"""Adding a runner by a setup code: the page that makes one, and its exchange for a runner machine.

A signed-in user asks the page for a code, saying which ladders the machine is to serve. The machine exchanges the code
for itself and its token, by `sc2scala runner register --setup-code`, with no other token. So a code is as random as a
token, kept as its hash, works once, and only for an hour.
"""

from datetime import timedelta
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from sc2scala.api_tokens import hash_of_token, new_runner_token, new_setup_code
from sc2scala.server.bots_api import commit_or_conflict
from sc2scala.server.ladders_api import visible_ladders
from sc2scala.server.models import Ladder, RunnerMachine, RunnerSetupCode, User, utc_now
from sc2scala.server.runners_api import (
    check_machine_name,
    ladders_named,
    machine_named,
    machine_summary,
    new_machine,
    void_leased_matches,
)
from sc2scala.server.web import DatabaseSession, check_csrf, refusal_reason, render, required_user

router = APIRouter()

SETUP_CODE_LIFETIME = timedelta(hours=1)
MOST_SETUP_CODES_WAITING = 10
"""How many unused codes one user may hold at once, so that codes nobody uses cannot fill the database."""
_ADD_RUNNER_PAGE = "/runners/add"
_NO_SUCH_CODE = "No setup code like that waits: it was used already, or expired; make another on the Add a runner page"

SignedInUser = Annotated[User, Depends(required_user)]


class SetupCodeExchange(BaseModel):
    setup_code: str
    name: str
    replace: bool = False
    """Whether a machine of the user's of the same name becomes this computer, as one set up again does."""


@router.get(_ADD_RUNNER_PAGE, response_class=HTMLResponse)
def add_runner_page(request: Request, session: DatabaseSession, user: SignedInUser, ladder: str = "") -> HTMLResponse:
    """The form that makes a setup code: every public ladder ticked, or, from a ladder's page, that ladder alone."""
    servable_ladders = visible_ladders(session, user)
    from_ladder = next((each for each in servable_ladders if each.name.lower() == ladder.lower()), None)
    return _render_form(
        request,
        user,
        servable_ladders,
        ticked_public=from_ladder is None,
        ticked_ladders={from_ladder.name} if from_ladder else set(),
    )


@router.post(_ADD_RUNNER_PAGE, response_class=HTMLResponse)
def make_setup_code(
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    csrf_token: Annotated[str, Form()],
    serves_public: Annotated[bool, Form()] = False,
    ladders: Annotated[list[str], Form()] = [],  # noqa: B006, FastAPI copies a form's default for each request
) -> HTMLResponse:
    """Makes a setup code for a machine serving every public ladder if `serves_public`, and `ladders`; the page that
    answers shows it, and never again, nor keeps it in the browser's cache. A refused form comes back as it was sent,
    with why."""
    check_csrf(request, csrf_token)
    try:
        code, setup_code = create_setup_code(session, user, serves_public=serves_public, ladder_names=ladders)
    except HTTPException as error:
        return _render_form(
            request,
            user,
            visible_ladders(session, user),
            ticked_public=serves_public,
            ticked_ladders=set(ladders),
            refusal=refusal_reason(error),
            status_code=error.status_code,
        )
    page = render(request, "runner_add.html", user=user, code=code, setup_code=setup_code)
    page.headers["Cache-Control"] = "no-store"
    return page


@router.post("/api/runners/setup")
def register_by_setup_code_route(session: DatabaseSession, exchange: SetupCodeExchange) -> dict[str, Any]:
    """Registers the machine a setup code is for, named `name`, for the code's user, and answers with its token, once.

    Needs no other token. 404 for a code used, expired or never made; 409 for a name the user's machines have already,
    unless `replace`; 422 for a name that is no machine's name.
    """
    machine, token = register_by_setup_code(
        session, code=exchange.setup_code, name=exchange.name, replace=exchange.replace
    )
    return {**machine_summary(session, machine), "token": token}


def _render_form(
    request: Request,
    user: User,
    servable_ladders: list[Ladder],
    *,
    ticked_public: bool,
    ticked_ladders: set[str],
    refusal: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "runner_add.html",
        user=user,
        servable_ladders=servable_ladders,
        ticked_public=ticked_public,
        ticked_ladders=ticked_ladders,
        refusal=refusal,
        code=None,
        status_code=status_code,
    )


def create_setup_code(
    session: Session, user: User, *, serves_public: bool, ladder_names: list[str]
) -> tuple[str, RunnerSetupCode]:
    """A new setup code of the user's, for a machine serving every public ladder if `serves_public`, and the ladders
    named, and its row, which holds its hash alone.

    404, 403 or 409 for a ladder the user may not serve; 422 for a machine that would serve nothing; 429 while the user
    holds `MOST_SETUP_CODES_WAITING` unused codes.
    """
    now = utc_now()
    session.execute(delete(RunnerSetupCode).where(RunnerSetupCode.expires_at < now))
    waiting = select(func.count()).select_from(RunnerSetupCode).where(RunnerSetupCode.user_id == user.id)
    if (session.scalar(waiting) or 0) >= MOST_SETUP_CODES_WAITING:
        raise HTTPException(
            429, f"You hold {MOST_SETUP_CODES_WAITING} unused setup codes: use them, or wait an hour for them to expire"
        )
    ladders = ladders_named(session, ladder_names, user)
    if not serves_public and not ladders:
        raise HTTPException(422, "A runner serves every public ladder, or the ladders ticked: tick one at least")
    code = new_setup_code()
    setup_code = RunnerSetupCode(
        user_id=user.id,
        code_hash=hash_of_token(code),
        serves_public=serves_public,
        created_at=now,
        expires_at=now + SETUP_CODE_LIFETIME,
        ladders=ladders,
    )
    session.add(setup_code)
    session.commit()
    return code, setup_code


def register_by_setup_code(session: Session, *, code: str, name: str, replace: bool) -> tuple[RunnerMachine, str]:
    """Registers the machine `code` is for, named `name`, and uses the code up; returns the machine and its token.

    With `replace`, a machine of the user's named `name` gets the new token and the code's ladders, and the matches it
    was playing, which the computer it was will not report, are void. 404, 409 and 422 as the route says, and 403, 404
    or 409 for a ladder of the code's the user may no longer serve; the code stays unused after a refusal.
    """
    check_machine_name(name)
    setup_code = session.scalar(select(RunnerSetupCode).where(RunnerSetupCode.code_hash == hash_of_token(code.strip())))
    if setup_code is None or setup_code.expires_at < utc_now():
        raise HTTPException(404, _NO_SUCH_CODE)
    user = setup_code.user
    serves_public = setup_code.serves_public
    ladders = ladders_named(session, [ladder.name for ladder in setup_code.ladders], user)
    if not serves_public and not ladders:
        raise HTTPException(409, "The ladders this setup code was for are gone: make another")
    machine = machine_named(session, user, name)
    if machine is not None and not replace:
        raise HTTPException(
            409,
            f"{user.login} has a machine named {name} already: replace it, if this computer is that machine set up "
            "again, or give this one another name",
        )
    used = cast(CursorResult[Any], session.execute(delete(RunnerSetupCode).where(RunnerSetupCode.id == setup_code.id)))
    if used.rowcount != 1:
        session.rollback()
        raise HTTPException(404, _NO_SUCH_CODE)
    if machine is None:
        machine, token = new_machine(user, name, serves_public=serves_public, ladders=ladders)
        session.add(machine)
    else:
        void_leased_matches(session, machine, because="its runner machine was set up again")
        token = new_runner_token()
        machine.token_hash = hash_of_token(token)
        machine.serves_public = serves_public
        machine.ladders = ladders
    try:
        commit_or_conflict(session, f"Another machine named {name} was registered at the same moment; try again")
    except StaleDataError as error:
        session.rollback()
        raise HTTPException(409, f"{user.login}'s machine {name} was removed at the same moment; try again") from error
    return machine, token
