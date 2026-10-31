"""The forms of a runner machine's owner on their page: which ladders it serves, and removing it.

Registering a machine happens on the machine, where its token is kept: by a setup code from the Add a runner page,
which `runner_setup` makes, or by `sc2scala runner register` signed in.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from sc2scala.server.models import User
from sc2scala.server.runners_api import change_machine_settings, remove_machine_named
from sc2scala.server.web import DatabaseSession, check_csrf, redirect_with_notice, refusal_reason, required_user

router = APIRouter()

SignedInUser = Annotated[User, Depends(required_user)]
_ACCOUNT_PAGE = "/account"


@router.post("/account/machines/{name}")
def change_machine(
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    csrf_token: Annotated[str, Form()],
    serves_public: Annotated[bool, Form()] = False,
    ladders: Annotated[list[str], Form()] = [],  # noqa: B006, FastAPI copies a form's default for each request
) -> RedirectResponse:
    """Sets what the user's machine serves: every public ladder if `serves_public`, and `ladders`."""
    check_csrf(request, csrf_token)
    try:
        machine = change_machine_settings(session, user, name, serves_public=serves_public, ladders=ladders)
    except HTTPException as error:
        return redirect_with_notice(request, _ACCOUNT_PAGE, refusal_reason(error), refused=True)
    serves = [*(["every public ladder"] if machine.serves_public else []), *(ladder.name for ladder in machine.ladders)]
    return redirect_with_notice(request, _ACCOUNT_PAGE, f"{machine.name} serves {', '.join(serves) or 'no ladder'}")


@router.post("/account/machines/{name}/remove")
def remove_machine(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    """Removes the user's machine, whose token then fails; the matches it was playing are void."""
    check_csrf(request, csrf_token)
    try:
        remove_machine_named(session, user, name)
    except HTTPException as error:
        return redirect_with_notice(request, _ACCOUNT_PAGE, refusal_reason(error), refused=True)
    return redirect_with_notice(request, _ACCOUNT_PAGE, f"Removed {name}; the matches it was playing are void")
