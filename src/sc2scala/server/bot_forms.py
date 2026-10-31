"""The pages' forms for bots and their entries: what the CLI does with a token, done by whoever is signed in.

Each form answers by sending the browser back to a page, which says once what the form did, or why it was refused.
The rules are the API's: each form calls the function its API route calls.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from sc2scala.errors import UploadRefusedError
from sc2scala.server.bots_api import bot_named, remove_bot_named, upload_bot_version
from sc2scala.server.ladders_api import (
    enter_in_ladder,
    join_with_password,
    pause_in_ladder,
    resume_in_ladder,
    withdraw_from_ladder,
)
from sc2scala.server.models import User
from sc2scala.server.web import (
    DatabaseSession,
    check_csrf,
    local_path_or,
    redirect_with_notice,
    refusal_reason,
    required_user,
)

router = APIRouter()

SignedInUser = Annotated[User, Depends(required_user)]
CsrfToken = Annotated[str, Form()]
NextPage = Annotated[str, Form()]


@router.post("/bots")
def new_bot(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: Annotated[str, Form()] = "",
    race: Annotated[str, Form()],
    archive: UploadFile,
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Uploads the first version of a bot of the user's, from the account page."""
    check_csrf(request, csrf_token)
    name = name.strip()
    existing = bot_named(session, name)
    if existing is not None and existing.owner_id == user.id:
        return redirect_with_notice(
            request,
            "/account",
            f"You have a bot named {existing.name} already: upload its next version on its page",
            refused=True,
            kept={"name": name, "race": race},
        )
    try:
        bot, version, _ = upload_bot_version(request, session, user, name, archive.file, race=race)
    except (HTTPException, UploadRefusedError) as error:
        return redirect_with_notice(
            request, "/account", refusal_reason(error), refused=True, kept={"name": name, "race": race}
        )
    return redirect_with_notice(request, f"/bots/{bot.name}", f"Uploaded {bot.name}, version {version.number}")


@router.post("/bots/{name}/versions")
def new_version(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    archive: UploadFile,
    csrf_token: CsrfToken,
    race: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Uploads the next version of the user's bot, and changes its race if `race` names one, from the bot's page."""
    check_csrf(request, csrf_token)
    try:
        bot, version, is_new = upload_bot_version(request, session, user, name, archive.file, race=race or None)
    except (HTTPException, UploadRefusedError) as error:
        return redirect_with_notice(request, f"/bots/{name}", refusal_reason(error), refused=True)
    message = (
        f"Uploaded version {version.number}"
        if is_new
        else f"That zip is version {version.number} already, so no version was added"
    )
    return redirect_with_notice(request, f"/bots/{bot.name}", message)


@router.post("/bots/{name}/remove")
def remove_bot(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    """Deletes the user's bot if it never played, or else retires it, from the bot's page."""
    check_csrf(request, csrf_token)
    try:
        removal = remove_bot_named(session, user, name)
    except HTTPException as error:
        return redirect_with_notice(request, f"/bots/{name}", refusal_reason(error), refused=True)
    if removal.retired:
        return redirect_with_notice(
            request, f"/bots/{removal.name}", f"Retired {removal.name}: it plays no more, and its matches stay"
        )
    return redirect_with_notice(request, "/account", f"Deleted {removal.name}")


@router.post("/bots/{name}/entries")
def enter_from_bot_page(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    ladder: Annotated[str, Form()] = "",
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Enters the user's bot in a ladder, from the bot's page."""
    check_csrf(request, csrf_token)
    try:
        entered_in = enter_in_ladder(session, user, ladder, name)
    except HTTPException as error:
        return redirect_with_notice(request, f"/bots/{name}", refusal_reason(error), refused=True)
    return redirect_with_notice(request, f"/bots/{name}", f"Entered {name} in {entered_in.name}")


@router.post("/ladders/{name}/join")
def join(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    password: Annotated[str, Form()] = "",
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Makes the user a member of a protected ladder, given its password, from the ladder's page."""
    check_csrf(request, csrf_token)
    try:
        ladder = join_with_password(session, user, name, password)
    except HTTPException as error:
        return redirect_with_notice(request, f"/ladders/{name}", refusal_reason(error), refused=True)
    return redirect_with_notice(request, f"/ladders/{ladder.name}", f"You are a member of {ladder.name}")


@router.post("/ladders/{name}/entries")
def enter_bot(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    bot: Annotated[str, Form()] = "",
    csrf_token: CsrfToken,
    next_page: NextPage = "",
) -> RedirectResponse:
    """Enters a bot of the user's in the ladder, from the ladder's page or the bot's."""
    check_csrf(request, csrf_token)
    back = local_path_or(next_page, f"/ladders/{name}")
    try:
        ladder = enter_in_ladder(session, user, name, bot)
    except HTTPException as error:
        return redirect_with_notice(request, back, refusal_reason(error), refused=True)
    return redirect_with_notice(request, back, f"Entered {bot} in {ladder.name}")


@router.post("/ladders/{name}/entries/{bot_name}/withdraw")
def withdraw_bot(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    bot_name: str,
    csrf_token: CsrfToken,
    next_page: NextPage = "",
) -> RedirectResponse:
    """Takes a bot out of the ladder, for its author or the ladder's owner; entering it again brings it back."""
    check_csrf(request, csrf_token)
    back = local_path_or(next_page, f"/ladders/{name}")
    try:
        ladder = withdraw_from_ladder(session, user, name, bot_name)
    except HTTPException as error:
        return redirect_with_notice(request, back, refusal_reason(error), refused=True)
    return redirect_with_notice(request, back, f"Took {bot_name} out of {ladder.name}")


@router.post("/ladders/{name}/entries/{bot_name}/pause")
def pause_entry(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    bot_name: str,
    csrf_token: CsrfToken,
    next_page: NextPage = "",
) -> RedirectResponse:
    """Pauses the author's entry, which then plays no match until resumed."""
    check_csrf(request, csrf_token)
    back = local_path_or(next_page, f"/ladders/{name}")
    try:
        entry = pause_in_ladder(session, user, name, bot_name)
    except HTTPException as error:
        return redirect_with_notice(request, back, refusal_reason(error), refused=True)
    return redirect_with_notice(request, back, f"Paused {entry.bot.name} in {entry.ladder.name}")


@router.post("/ladders/{name}/entries/{bot_name}/resume")
def resume_entry(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    bot_name: str,
    csrf_token: CsrfToken,
    next_page: NextPage = "",
) -> RedirectResponse:
    """Lets the author's paused entry play again."""
    check_csrf(request, csrf_token)
    back = local_path_or(next_page, f"/ladders/{name}")
    try:
        entry = resume_in_ladder(session, user, name, bot_name)
    except HTTPException as error:
        return redirect_with_notice(request, back, refusal_reason(error), refused=True)
    return redirect_with_notice(request, back, f"{entry.bot.name} plays in {entry.ladder.name} again")
