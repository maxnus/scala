"""The API runner machines call with their own tokens: a match to play, its files, and its result, and removing
themselves.

A runner asks for a match at `/api/runner/ask`, from step 9 on, and is answered with its owner's settings too; one of an
earlier release asks at `/api/runner/matches`, which leaves it as it was.
"""

import json
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sc2scala.engine_names import ENGINES_PLAYING_COMPUTERS
from sc2scala.errors import UploadRefusedError
from sc2scala.game_limits import GameLimits
from sc2scala.resource_limits import ResourceLimits
from sc2scala.result_code import ResultCode
from sc2scala.runner_config import MOST_MATCHES_AT_ONCE
from sc2scala.server.bots_api import BOT_ARCHIVE_SUFFIX, BOT_ARCHIVES
from sc2scala.server.file_store import FileStore
from sc2scala.server.ladder_ratings import refit_after_result
from sc2scala.server.ladders_api import MAP_FILES, computer_player
from sc2scala.server.match_draw import (
    LEASED,
    VOID,
    draw_match,
    leased_matches,
    plays_fewer_than_it_may,
    record_result,
    void_expired_leases,
    void_leases_not_played,
)
from sc2scala.server.models import MatchRecord, RunnerMachine, utc_now
from sc2scala.server.runners_api import remove_runner_machine
from sc2scala.server.upload_checks import MAP_SUFFIX
from sc2scala.server.web import DatabaseSession, runner_machine

router = APIRouter(prefix="/api/runner")

REPLAYS = "replays"
REPLAY_SUFFIX = ".SC2Replay"
LOGS = "logs"
LOGS_SUFFIX = ".zip"
_REPLAY_SIGNATURE = b"MPQ"
_MEBIBYTE = 1024 * 1024
_LOG_FIELDS = ("player_1_logs", "player_2_logs", "engine_logs")

Machine = Annotated[RunnerMachine, Depends(runner_machine)]


class FailureReport(BaseModel):
    reason: str


class MatchRequest(BaseModel):
    """How a runner of a release before step 9 asks for a match."""

    engine: str | None = None
    """The name of the engine the runner plays with; a runner of a release before computer opponents names none."""


class Ask(BaseModel):
    """What a runner says each time it asks for a match, from step 9 on."""

    engine: str = Field(max_length=20)
    playing: list[int] = Field(max_length=MOST_MATCHES_AT_ONCE)
    """The matches it plays, each of them, since it asks one at a time."""
    max_matches_at_once: int = Field(ge=1, le=MOST_MATCHES_AT_ONCE)
    """The most it may play at once, as its machine allows."""
    scala_version: str = Field(max_length=40)
    next_window_opens: datetime | None = None
    """When its next window opens, in its own time, if none is open; it takes no match then."""


@router.delete("/machine")
def remove_this_machine(session: DatabaseSession, machine: Machine) -> dict[str, str]:
    """Removes the machine whose token this is, which then fails, and voids the matches it was playing."""
    name = machine.runner_name
    remove_runner_machine(session, machine)
    return {"removed": name}


@router.post("/ask")
def ask_for_a_match(request: Request, session: DatabaseSession, machine: Machine, ask: Ask) -> dict[str, Any]:
    """A match drawn for the machine and leased to it, if it may play another, or none; and how many it may play at
    once and whether it is paused, as its owner set them.

    It may while it is not paused, a window of its own is open, and it plays fewer than its owner's number, at most the
    most it allows; first, the leases it does not name as being played are void, as `void_leases_not_played` says.
    What it says of itself is kept for its owner's page.
    """
    now = utc_now()
    void_expired_leases(session, now=now)
    void_leases_not_played(session, machine, playing=ask.playing, now=now)
    machine.max_matches_at_once = ask.max_matches_at_once
    machine.scala_version = ask.scala_version
    machine.engine = ask.engine
    machine.next_window_opens = f"{ask.next_window_opens:%Y-%m-%dT%H:%M}" if ask.next_window_opens else None
    match = None
    if not machine.paused and ask.next_window_opens is None and plays_fewer_than_it_may(session, machine):
        match = _drawn_for(request, session, machine, now=now, engine=ask.engine)
    machine.last_answered_at = utc_now()
    session.commit()
    return {
        "match": match_for_runner(match) if match is not None else None,
        "matches_at_once": machine.matches_at_once,
        "paused": machine.paused,
    }


@router.post("/matches", response_model=None)
def next_match(
    request: Request, session: DatabaseSession, machine: Machine, asked: MatchRequest | None = None
) -> dict[str, Any] | Response:
    """For a runner of a release before step 9: a match drawn for the machine and leased to it, or 204 if no ladder it
    serves has a pair that may play; a match against the computer only if the engine it names plays one.

    Such a runner plays as many at once as its own file says, and is not paused; the machine no longer reads as
    following its owner's settings, should a runner of step 9 have asked for it before.
    """
    now = utc_now()
    void_expired_leases(session, now=now)
    machine.scala_version = None
    match = _drawn_for(request, session, machine, now=now, engine=asked.engine if asked is not None else None)
    session.commit()
    if match is None:
        return Response(status_code=204)
    return match_for_runner(match)


@router.get("/bots/{sha256}")
def bot_archive(request: Request, session: DatabaseSession, machine: Machine, sha256: str) -> FileResponse:
    """A bot's zip, for a machine playing a match of it alone."""
    leased = leased_matches(session, machine)
    if not any(version.sha256 == sha256 for match in leased for version in match.bot_versions):
        raise HTTPException(403, "The machine plays no match of that bot")
    file_store: FileStore = request.app.state.file_store
    return FileResponse(file_store.path_of(BOT_ARCHIVES, sha256, BOT_ARCHIVE_SUFFIX), media_type="application/zip")


@router.get("/maps/{sha256}")
def map_file(request: Request, session: DatabaseSession, machine: Machine, sha256: str) -> FileResponse:
    """A map, for a machine playing a match on it alone."""
    if not any(match.map_file.sha256 == sha256 for match in leased_matches(session, machine)):
        raise HTTPException(403, "The machine plays no match on that map")
    file_store: FileStore = request.app.state.file_store
    return FileResponse(file_store.path_of(MAP_FILES, sha256, MAP_SUFFIX), media_type="application/octet-stream")


@router.post("/matches/{match_id}/result")
def report_result(
    request: Request,
    session: DatabaseSession,
    machine: Machine,
    match_id: int,
    background: BackgroundTasks,
    result: Annotated[str, Form()],
    replay: UploadFile | None = None,
    player_1_logs: UploadFile | None = None,
    player_2_logs: UploadFile | None = None,
    engine_logs: UploadFile | None = None,
) -> dict[str, Any]:
    """Finishes a match leased to the machine with its result, a JSON object of `code`, `game_steps` and
    `average_step_times`, and keeps its replay and logs, but for player 2's against the computer, which has none; the
    ladder's ratings are refitted once the runner is answered.

    409 once the match is void, its deadline passed among others.
    """
    match = _leased_match(session, machine, match_id)
    code, game_steps, average_step_times = _parsed_result(result)
    file_store: FileStore = request.app.state.file_store
    max_bytes = request.app.state.config.max_result_upload_mib * _MEBIBYTE
    if replay is not None:
        stored, _ = file_store.store(
            replay.file, kind=REPLAYS, suffix=REPLAY_SUFFIX, max_bytes=max_bytes, check=_check_replay
        )
        match.replay_sha256 = stored.sha256
    if match.player_2_computer_id is not None:
        player_2_logs = None
    for field_name, upload in zip(_LOG_FIELDS, (player_1_logs, player_2_logs, engine_logs), strict=True):
        if upload is not None:
            stored, _ = file_store.store(
                upload.file, kind=LOGS, suffix=LOGS_SUFFIX, max_bytes=max_bytes, check=_check_zip
            )
            setattr(match, f"{field_name}_sha256", stored.sha256)
    match.game_steps = game_steps
    match.player_1_average_step_time, match.player_2_average_step_time = average_step_times
    record_result(session, match, code, now=utc_now())
    session.commit()
    background.add_task(refit_after_result, request.app, match.ladder_id)
    return {"id": match.id, "status": match.status, "result": match.result}


@router.post("/matches/{match_id}/failure")
def report_failure(session: DatabaseSession, machine: Machine, match_id: int, failure: FailureReport) -> dict[str, Any]:
    """Voids a match leased to the machine, which could not play it."""
    match = _leased_match(session, machine, match_id)
    match.status = VOID
    match.void_because = f"its runner could not play it: {failure.reason}"[:300]
    match.finished_at = utc_now()
    session.commit()
    return {"id": match.id, "status": match.status}


def match_for_runner(match: MatchRecord) -> dict[str, Any]:
    """What a runner needs to play `match`: its bots' and map's files by their hashes, the computer in seat 2 if it
    plays one, and its limits.

    A limit that is off is left out, so that a runner of an earlier release, which knows no limit to be off, takes its
    own default instead: AI Arena's engine passes the step-time limit on and applies it not, and a `None` written into
    its config would break every match.
    """
    game_limits = {
        name: value for name, value in asdict(GameLimits(**match.ladder.game_limits)).items() if value is not None
    }
    races = (match.player_1_race, match.player_2_race)
    players = [
        {
            "seat": seat,
            "bot": version.bot.name,
            "race": race,
            "type": version.bot_type,
            "version": version.number,
            "sha256": version.sha256,
        }
        for seat, (version, race) in enumerate(zip(match.bot_versions, races, strict=False), start=1)
    ]
    if match.player_2_computer is not None:
        players.append({"seat": 2, **computer_player(match.player_2_computer)})
    return {
        "id": match.id,
        "ladder": match.ladder.name,
        "deadline": match.deadline.isoformat(),
        "map": {"name": match.map_file.name, "sha256": match.map_file.sha256},
        "players": players,
        "game_limits": game_limits,
        "resource_limits": asdict(ResourceLimits(**match.ladder.resource_limits)),
    }


def _drawn_for(
    request: Request, session: Session, machine: RunnerMachine, *, now: datetime, engine: str | None
) -> MatchRecord | None:
    """A match drawn for the machine, which plays with `engine`, and leased to it, uncommitted; or `None`."""
    return draw_match(
        session,
        machine,
        rng=request.app.state.match_random,
        now=now,
        lease_margin=timedelta(seconds=request.app.state.config.lease_margin_seconds),
        plays_computers=engine in ENGINES_PLAYING_COMPUTERS,
    )


def _leased_match(session: Session, machine: RunnerMachine, match_id: int) -> MatchRecord:
    """The match, leased to the machine and still before its deadline; 404 if it is not the machine's, 409 if void."""
    now = utc_now()
    void_expired_leases(session, now=now)
    session.flush()
    match = session.get(MatchRecord, match_id)
    if match is None or match.machine_id != machine.id:
        raise HTTPException(404, f"The machine plays no match {match_id}")
    session.refresh(match)
    if match.status != LEASED:
        session.commit()
        raise HTTPException(409, f"Match {match_id} is {match.status}: {match.void_because or 'reported already'}")
    return match


def _parsed_result(text: str) -> tuple[ResultCode, int, tuple[float | None, float | None]]:
    try:
        result = json.loads(text)
        code = ResultCode(result["code"])
        game_steps = int(result.get("game_steps", 0))
        step_times = result.get("average_step_times", [None, None])
        average_step_times = (_optional_float(step_times[0]), _optional_float(step_times[1]))
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise UploadRefusedError(
            f"A result is a JSON object of code, game_steps and average_step_times: {error}"
        ) from error
    return code, game_steps, average_step_times


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _check_replay(path: Path) -> None:
    with path.open("rb") as replay:
        if replay.read(len(_REPLAY_SIGNATURE)) != _REPLAY_SIGNATURE:
            raise UploadRefusedError("The replay is not a StarCraft II replay")


def _check_zip(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise UploadRefusedError("The logs are not a zip")
