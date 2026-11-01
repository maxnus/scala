"""The pages and forms of a ladder's owner: making a ladder, its settings, maps and computers, barring its runners,
and removing it.

As the bots' forms do, each form calls the function its API route calls, and sends the browser back to a page that
says once what it did, or why it was refused.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from sc2scala.computer import Build, Difficulty
from sc2scala.errors import UploadRefusedError
from sc2scala.race import Race
from sc2scala.server.computers_api import (
    ComputerRequest,
    add_computer_to_ladder,
    computer_asked,
    pause_computer_in_ladder,
    remove_computer_from_ladder,
)
from sc2scala.server.ladder_limits import LIMIT_GROUPS, limit_values
from sc2scala.server.ladder_ratings import playing_computers, refit_after_result
from sc2scala.server.ladder_runners_api import bar_runner_named, unbar_runner_named
from sc2scala.server.ladders_api import (
    add_maps,
    change_ladder_settings,
    ladder_named,
    make_ladder,
    matches_in_ladder,
    owned_ladder,
    remove_ladder_named,
    remove_map_named,
)
from sc2scala.server.models import User
from sc2scala.server.web import DatabaseSession, check_csrf, redirect_with_notice, refusal_reason, render, required_user

router = APIRouter()

SignedInUser = Annotated[User, Depends(required_user)]
CsrfToken = Annotated[str, Form()]
_NEW_LADDER_PAGE = "/ladders/new"


def limit_changes_from_form(
    max_game_steps: Annotated[str, Form()] = "",
    max_step_time_ms: Annotated[str, Form()] = "",
    step_time_strikes: Annotated[str, Form()] = "",
    max_real_time_seconds: Annotated[str, Form()] = "",
    bot_cpus: Annotated[str, Form()] = "",
    bot_memory_mib: Annotated[str, Form()] = "",
    bot_processes: Annotated[str, Form()] = "",
    bot_log_mib: Annotated[str, Form()] = "",
    game_cpus: Annotated[str, Form()] = "",
    game_memory_mib: Annotated[str, Form()] = "",
    match_controller_cpus: Annotated[str, Form()] = "",
    match_controller_memory_mib: Annotated[str, Form()] = "",
) -> dict[str, str]:
    """The limits a form filled in, each by its name in `LIMIT_GROUPS`; one left empty is not changed, but one that
    may be off is turned off, since the forms that post limits show them all, and FastAPI reads an empty field as one
    left out."""
    given = {
        "max_game_steps": max_game_steps,
        "max_step_time_ms": max_step_time_ms,
        "step_time_strikes": step_time_strikes,
        "max_real_time_seconds": max_real_time_seconds,
        "bot_cpus": bot_cpus,
        "bot_memory_mib": bot_memory_mib,
        "bot_processes": bot_processes,
        "bot_log_mib": bot_log_mib,
        "game_cpus": game_cpus,
        "game_memory_mib": game_memory_mib,
        "match_controller_cpus": match_controller_cpus,
        "match_controller_memory_mib": match_controller_memory_mib,
    }
    return {name: value.strip() for name, value in given.items() if value.strip() or name in _OPTIONAL_LIMITS}


LimitChanges = Annotated[dict[str, str], Depends(limit_changes_from_form)]
_OPTIONAL_LIMITS = {field.name for _, group in LIMIT_GROUPS for field in group if field.optional}


@router.get(_NEW_LADDER_PAGE, response_class=HTMLResponse)
def new_ladder_page(request: Request, user: SignedInUser) -> HTMLResponse:
    """The form that makes a ladder, its limits filled in with the defaults."""
    return render(
        request,
        "ladder_new.html",
        user=user,
        limit_groups=LIMIT_GROUPS,
        limits=limit_values(game_limits={}, resource_limits={}),
    )


@router.post(_NEW_LADDER_PAGE)
def new_ladder(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: Annotated[str, Form()] = "",
    maps: list[UploadFile],
    limit_changes: LimitChanges,
    csrf_token: CsrfToken,
    password: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Makes the user's ladder of the maps uploaded, protected if given a password."""
    check_csrf(request, csrf_token)
    name = name.strip()
    try:
        ladder = make_ladder(
            request, session, user, name, maps, password=password or None, limit_changes=dict(limit_changes)
        )
    except (HTTPException, UploadRefusedError) as error:
        return redirect_with_notice(
            request, _NEW_LADDER_PAGE, refusal_reason(error), refused=True, kept={"name": name, **limit_changes}
        )
    return redirect_with_notice(request, f"/ladders/{ladder.name}", f"Made {ladder.name}")


@router.get("/ladders/{name}/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    """The owner's page of the ladder's password, maps, computers and limits, and of removing it."""
    ladder = owned_ladder(session, name, user)
    return render(
        request,
        "ladder_settings.html",
        user=user,
        ladder=ladder,
        computers=playing_computers(ladder),
        races=list(Race),
        difficulties=list(Difficulty),
        builds=list(Build),
        limit_groups=LIMIT_GROUPS,
        limits=limit_values(game_limits=ladder.game_limits, resource_limits=ladder.resource_limits),
        matches_drawn=matches_in_ladder(session, ladder),
    )


@router.post("/ladders/{name}/password")
def set_password(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    new_password: Annotated[str, Form()] = "",
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Protects the ladder with a new password; whoever joined before stays a member."""
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    if not new_password:
        return redirect_with_notice(request, settings, "A password cannot be empty", refused=True)
    try:
        ladder = change_ladder_settings(session, user, name, new_password=new_password)
    except HTTPException as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True)
    return redirect_with_notice(request, f"/ladders/{ladder.name}/settings", f"{ladder.name} has a new password")


@router.post("/ladders/{name}/password/remove")
def remove_password(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    """Makes the ladder public."""
    check_csrf(request, csrf_token)
    try:
        ladder = change_ladder_settings(session, user, name, remove_password=True)
    except HTTPException as error:
        return redirect_with_notice(request, f"/ladders/{name}/settings", refusal_reason(error), refused=True)
    return redirect_with_notice(request, f"/ladders/{ladder.name}/settings", f"{ladder.name} is public now")


@router.post("/ladders/{name}/limits")
def change_limits(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    limit_changes: LimitChanges,
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Changes the ladder's limits, which its next matches take."""
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    try:
        change_ladder_settings(session, user, name, limit_changes=dict(limit_changes))
    except (HTTPException, UploadRefusedError) as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True, kept=limit_changes)
    return redirect_with_notice(request, settings, "Changed the limits, which the next matches take")


@router.post("/ladders/{name}/maps")
def add_maps_to_ladder(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    maps: list[UploadFile],
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Adds the uploaded maps to the ladder's pool, none if one is refused."""
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    pool = ladder_named(session, name)
    held_before = {(map_file.name, map_file.sha256) for map_file in pool.maps} if pool else set()
    try:
        ladder = add_maps(request, session, user, name, maps)
    except (HTTPException, UploadRefusedError) as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True)
    added = [map_file.name for map_file in ladder.maps if (map_file.name, map_file.sha256) not in held_before]
    message = f"Added {', '.join(added)}" if added else f"{ladder.name} holds those maps already"
    return redirect_with_notice(request, settings, message)


@router.post("/ladders/{name}/maps/{map_name}/remove")
def remove_map_from_ladder(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, map_name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    """Takes a map out of the ladder's pool, but for its last."""
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    try:
        remove_map_named(session, user, name, map_name)
    except HTTPException as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True)
    return redirect_with_notice(request, settings, f"Took {map_name} out")


@router.post("/ladders/{name}/computers")
def add_computer(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    race: Annotated[str, Form()],
    difficulty: Annotated[str, Form()],
    build: Annotated[str, Form()] = Build.RANDOM.value,
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Adds the computer to the ladder, or brings it back if it was retired there."""
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    try:
        computer = computer_asked(ComputerRequest(race=race, difficulty=difficulty, build=build))
        add_computer_to_ladder(session, user, name, computer)
    except HTTPException as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True)
    return redirect_with_notice(request, settings, f"Added {computer.name}")


@router.post("/ladders/{name}/computers/{computer_name}/pause")
def pause_computer(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, computer_name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    return _paused_computer(request, session, user, name, computer_name, csrf_token, paused=True)


@router.post("/ladders/{name}/computers/{computer_name}/resume")
def resume_computer(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, computer_name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    return _paused_computer(request, session, user, name, computer_name, csrf_token, paused=False)


@router.post("/ladders/{name}/computers/{computer_name}/remove")
def remove_computer(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, computer_name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    """Deletes the computer from the ladder if it never played there, or else retires it."""
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    try:
        removal = remove_computer_from_ladder(session, user, name, computer_name)
    except HTTPException as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True)
    if removal.retired:
        return redirect_with_notice(
            request, settings, f"Retired {removal.name}, which keeps its {removal.matches} matches here"
        )
    return redirect_with_notice(request, settings, f"Deleted {removal.name}")


def _paused_computer(
    request: Request,
    session: DatabaseSession,
    user: User,
    name: str,
    computer_name: str,
    csrf_token: str,
    *,
    paused: bool,
) -> RedirectResponse:
    check_csrf(request, csrf_token)
    settings = f"/ladders/{name}/settings"
    try:
        held = pause_computer_in_ladder(session, user, name, computer_name, paused=paused)
    except HTTPException as error:
        return redirect_with_notice(request, settings, refusal_reason(error), refused=True)
    return redirect_with_notice(request, settings, f"{'Paused' if paused else 'Resumed'} {held.name}")


@router.post("/ladders/{name}/remove")
def remove_ladder(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, csrf_token: CsrfToken
) -> RedirectResponse:
    """Deletes the owner's ladder if it never drew a match, or else retires it."""
    check_csrf(request, csrf_token)
    try:
        removal = remove_ladder_named(session, user, name)
    except HTTPException as error:
        return redirect_with_notice(request, f"/ladders/{name}/settings", refusal_reason(error), refused=True)
    if removal.retired:
        return redirect_with_notice(
            request,
            f"/ladders/{removal.name}",
            f"Retired {removal.name}: it draws no more matches, and keeps its standings and matches",
        )
    return redirect_with_notice(request, "/", f"Deleted {removal.name}")


@router.post("/ladders/{name}/runners/bar")
def bar_runner(
    *,
    request: Request,
    background: BackgroundTasks,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    runner: Annotated[str, Form()],
    csrf_token: CsrfToken,
    keep_results: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    """Bars a runner from the ladder, voiding its results there unless `keep_results`, and refits the ratings."""
    check_csrf(request, csrf_token)
    runners_page = f"/ladders/{name}/runners"
    try:
        barred = bar_runner_named(session, user, name, runner, keep_results=keep_results)
    except HTTPException as error:
        return redirect_with_notice(request, runners_page, refusal_reason(error), refused=True)
    if barred.results_voided:
        background.add_task(refit_after_result, request.app, barred.ladder_id)
    voided = barred.results_voided
    outcome = "its results kept" if keep_results else f"{voided} {'result' if voided == 1 else 'results'} void"
    return redirect_with_notice(request, runners_page, f"Barred {barred.runner}, {outcome}")


@router.post("/ladders/{name}/runners/{owner}/{machine}/unbar")
def unbar_runner(
    *,
    request: Request,
    session: DatabaseSession,
    user: SignedInUser,
    name: str,
    owner: str,
    machine: str,
    csrf_token: CsrfToken,
) -> RedirectResponse:
    """Lets a barred runner play the ladder's matches again; what its bar voided stays void."""
    check_csrf(request, csrf_token)
    runners_page = f"/ladders/{name}/runners"
    try:
        runner = unbar_runner_named(session, user, name, owner, machine)
    except HTTPException as error:
        return redirect_with_notice(request, runners_page, refusal_reason(error), refused=True)
    return redirect_with_notice(request, runners_page, f"{runner} may play here again; what its bar voided stays void")
