"""The API's matches, as the users who may see their ladder read them, with a user's token."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.server.file_store import FileStore
from sc2scala.server.ladders_api import visible_ladder
from sc2scala.server.models import MatchRecord, User
from sc2scala.server.runner_api import LOGS, LOGS_SUFFIX, REPLAY_SUFFIX, REPLAYS
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api")

_MAX_MATCHES_LISTED = 200

TokenUser = Annotated[User, Depends(token_user)]


@router.get("/ladders/{name}/matches")
def ladder_matches(session: DatabaseSession, user: TokenUser, name: str, limit: int = 20) -> list[dict[str, Any]]:
    """The ladder's latest matches, the latest first."""
    ladder = visible_ladder(session, name, user)
    query = (
        select(MatchRecord)
        .where(MatchRecord.ladder_id == ladder.id)
        .order_by(MatchRecord.id.desc())
        .limit(max(1, min(limit, _MAX_MATCHES_LISTED)))
    )
    return [match_summary(match) for match in session.scalars(query)]


@router.get("/matches/{match_id}")
def match(session: DatabaseSession, user: TokenUser, match_id: int) -> dict[str, Any]:
    return match_summary(_visible_match(session, user, match_id))


@router.get("/matches/{match_id}/replay")
def match_replay(request: Request, session: DatabaseSession, user: TokenUser, match_id: int) -> FileResponse:
    seen = _visible_match(session, user, match_id)
    if seen.replay_sha256 is None:
        raise HTTPException(404, f"Match {match_id} has no replay")
    file_store: FileStore = request.app.state.file_store
    return FileResponse(
        file_store.path_of(REPLAYS, seen.replay_sha256, REPLAY_SUFFIX),
        media_type="application/octet-stream",
        filename=f"{match_id}_{seen.player_1_version.bot.name}_vs_{seen.player_2_version.bot.name}{REPLAY_SUFFIX}",
    )


@router.get("/matches/{match_id}/logs/{part}")
def match_logs(request: Request, session: DatabaseSession, user: TokenUser, match_id: int, part: str) -> FileResponse:
    """A bot's logs, `1` or `2` by its seat, for the bot's author alone, or the engine's, `engine`, for the ladder's
    owner and the runner machine's."""
    seen = _visible_match(session, user, match_id)
    if part in ("1", "2"):
        version = seen.versions[int(part) - 1]
        if version.bot.owner_id != user.id:
            raise HTTPException(403, f"{version.bot.name}'s logs are for {version.bot.owner.login} alone")
        sha256 = seen.player_1_logs_sha256 if part == "1" else seen.player_2_logs_sha256
    elif part == "engine":
        if user.id not in (seen.ladder.owner_id, seen.machine.owner_id if seen.machine else None):
            raise HTTPException(403, "The engine's logs are for the ladder's owner and the runner's")
        sha256 = seen.engine_logs_sha256
    else:
        raise HTTPException(404, "Logs are 1, 2 or engine")
    if sha256 is None:
        raise HTTPException(404, f"Match {match_id} has no such logs")
    file_store: FileStore = request.app.state.file_store
    return FileResponse(
        file_store.path_of(LOGS, sha256, LOGS_SUFFIX), media_type="application/zip", filename=f"{match_id}-{part}.zip"
    )


def match_summary(record: MatchRecord) -> dict[str, Any]:
    races = (record.player_1_race, record.player_2_race)
    return {
        "id": record.id,
        "ladder": record.ladder.name,
        "status": record.status,
        "map": record.map_file.name,
        "players": [
            {"bot": version.bot.name, "author": version.bot.owner.login, "version": version.number, "race": race}
            for version, race in zip(record.versions, races, strict=True)
        ],
        "result": record.result,
        "game_steps": record.game_steps,
        "drawn_at": record.drawn_at.isoformat(),
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "void_because": record.void_because,
        "runner": f"{record.machine.owner.login}/{record.machine.name}" if record.machine else None,
        "replay": record.replay_sha256 is not None,
    }


def _visible_match(session: Session, user: User, match_id: int) -> MatchRecord:
    record = session.get(MatchRecord, match_id)
    if record is None:
        raise HTTPException(404, f"No match {match_id}")
    visible_ladder(session, record.ladder.name, user)
    return record
