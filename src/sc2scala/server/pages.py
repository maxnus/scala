"""The server's pages: its ladders, a ladder's standings, matches and pairs, a match, and a bot; they only show."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from sc2scala.result_code import ResultCode
from sc2scala.server.bots_api import bot_named
from sc2scala.server.ladder_ratings import (
    INTERVAL_WIDTH,
    PRIOR_SD,
    pair_records,
    rating_of_latest_version,
    standings,
)
from sc2scala.server.ladders_api import is_member, visible_ladder
from sc2scala.server.match_draw import LEASED, VOID
from sc2scala.server.matches_api import (
    logs_of,
    match_summary,
    may_read_bot_logs,
    may_read_engine_logs,
    replay_of,
    visible_match,
)
from sc2scala.server.models import Ladder, LadderEntry, MatchRecord, User
from sc2scala.server.web import DatabaseSession, render, signed_in_user

router = APIRouter()

_MATCHES_ON_LADDER_PAGE = 20
_MATCHES_PER_PAGE = 50

SignedInUser = Annotated[User | None, Depends(signed_in_user)]


@router.get("/", response_class=HTMLResponse)
def ladders_page(request: Request, session: DatabaseSession, user: SignedInUser) -> HTMLResponse:
    ladders = session.scalars(
        select(Ladder)
        .options(selectinload(Ladder.owner), selectinload(Ladder.entries), selectinload(Ladder.members))
        .order_by(Ladder.name)
    )
    shown = [
        {
            "name": ladder.name,
            "owner": ladder.owner.login,
            "protected": ladder.is_protected,
            "visible": not ladder.is_protected or is_member(ladder, user),
            "bots": len(ladder.entries),
        }
        for ladder in ladders
    ]
    return render(request, "home.html", user=user, ladders=shown)


@router.get("/ladders/{name}", response_class=HTMLResponse)
def ladder_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    ladder = visible_ladder(session, name, user)
    return render(
        request,
        "ladder.html",
        user=user,
        ladder=ladder,
        standings=standings(session, ladder),
        matches=_shown_matches(_latest_matches(session, ladder, limit=_MATCHES_ON_LADDER_PAGE)),
        interval_width=INTERVAL_WIDTH,
        prior_sd=PRIOR_SD,
    )


@router.get("/ladders/{name}/matches", response_class=HTMLResponse)
def ladder_matches_page(
    request: Request, session: DatabaseSession, user: SignedInUser, name: str, before: int | None = None
) -> HTMLResponse:
    ladder = visible_ladder(session, name, user)
    matches = _latest_matches(session, ladder, limit=_MATCHES_PER_PAGE, before=before)
    earlier = matches[-1].id if len(matches) == _MATCHES_PER_PAGE else None
    return render(
        request, "ladder_matches.html", user=user, ladder=ladder, matches=_shown_matches(matches), earlier=earlier
    )


@router.get("/ladders/{name}/pairs", response_class=HTMLResponse)
def pairs_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    ladder = visible_ladder(session, name, user)
    return render(request, "pairs.html", user=user, ladder=ladder, pairs=pair_records(session, ladder))


@router.get("/matches/{match_id}", response_class=HTMLResponse)
def match_page(request: Request, session: DatabaseSession, user: SignedInUser, match_id: int) -> HTMLResponse:
    record = visible_match(session, user, match_id)
    return render(
        request,
        "match.html",
        user=user,
        match=_shown_match(record),
        readable_logs=[seat for seat in (1, 2) if may_read_bot_logs(record, user, seat=seat)],
        engine_logs=may_read_engine_logs(record, user),
    )


@router.get("/matches/{match_id}/replay")
def replay_download(request: Request, session: DatabaseSession, user: SignedInUser, match_id: int) -> FileResponse:
    return replay_of(request, session, user, match_id)


@router.get("/matches/{match_id}/logs/{part}")
def logs_download(
    request: Request, session: DatabaseSession, user: SignedInUser, match_id: int, part: str
) -> FileResponse:
    return logs_of(request, session, user, match_id, part)


@router.get("/bots/{name}", response_class=HTMLResponse)
def bot_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    bot = bot_named(session, name)
    if bot is None:
        raise HTTPException(404, f"No bot is named {name}")
    entries = session.scalars(
        select(LadderEntry).join(Ladder).where(LadderEntry.bot_id == bot.id).order_by(Ladder.name)
    )
    ladders = [
        {"ladder": entry.ladder, "standing": rating_of_latest_version(session, entry.ladder, entry)}
        for entry in entries
        if not entry.ladder.is_protected or is_member(entry.ladder, user)
    ]
    visible_ids = [entry["ladder"].id for entry in ladders]
    version_ids = [version.id for version in bot.versions]
    matches = session.scalars(
        select(MatchRecord)
        .where(
            MatchRecord.ladder_id.in_(visible_ids),
            MatchRecord.player_1_version_id.in_(version_ids) | MatchRecord.player_2_version_id.in_(version_ids),
        )
        .order_by(MatchRecord.id.desc())
        .limit(_MATCHES_ON_LADDER_PAGE)
    )
    return render(request, "bot.html", user=user, bot=bot, ladders=ladders, matches=_shown_matches(list(matches)))


def outcome_of(record: MatchRecord) -> str:
    """The match's outcome in words, naming the bot it concerns."""
    if record.status == LEASED:
        return "being played"
    if record.status == VOID:
        return f"void: {record.void_because}" if record.void_because else "void"
    bots = [version.bot.name for version in record.versions]
    code = ResultCode(record.result) if record.result else None
    winner = {ResultCode.PLAYER_1_WIN: bots[0], ResultCode.PLAYER_2_WIN: bots[1]}.get(code) if code else None
    if winner is not None:
        return f"{winner} won"
    described = {
        ResultCode.TIE: "a tie",
        ResultCode.PLAYER_1_CRASH: f"{bots[0]} crashed",
        ResultCode.PLAYER_2_CRASH: f"{bots[1]} crashed",
        ResultCode.PLAYER_1_TIME_OUT: f"{bots[0]} took too long",
        ResultCode.PLAYER_2_TIME_OUT: f"{bots[1]} took too long",
        ResultCode.INITIALIZATION_ERROR: "a bot or the game did not start",
        ResultCode.ERROR: "the engine failed",
    }
    return described.get(code, "finished") if code else "finished"


def _latest_matches(session: Session, ladder: Ladder, *, limit: int, before: int | None = None) -> list[MatchRecord]:
    query = select(MatchRecord).where(MatchRecord.ladder_id == ladder.id)
    if before is not None:
        query = query.where(MatchRecord.id < before)
    return list(session.scalars(query.order_by(MatchRecord.id.desc()).limit(limit)))


def _shown_matches(records: list[MatchRecord]) -> list[dict[str, Any]]:
    return [_shown_match(record) for record in records]


def _shown_match(record: MatchRecord) -> dict[str, Any]:
    return {**match_summary(record), "outcome": outcome_of(record), "drawn": record.drawn_at, "record": record}
