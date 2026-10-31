"""The server's pages: its ladders and bots, a ladder's standings, matches and pairs, a match, and a bot; they only
show."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from sc2scala.race import Race
from sc2scala.result_code import ResultCode
from sc2scala.server.bots_api import bot_named, matches_of_bot, playing_bots_of
from sc2scala.server.ladder_ratings import (
    INTERVAL_WIDTH,
    PRIOR_SD,
    pair_records,
    rating_of_latest_version,
    standings,
)
from sc2scala.server.ladder_runners_api import runners_of
from sc2scala.server.ladders_api import (
    is_member,
    ladder_named,
    may_enter,
    may_withdraw,
    visible_ladder,
    visible_ladders,
)
from sc2scala.server.match_draw import LEASED, VOID
from sc2scala.server.matches_api import (
    logs_of,
    match_summary,
    may_read_bot_logs,
    may_read_engine_logs,
    replay_of,
    visible_match,
)
from sc2scala.server.models import BotRecord, Ladder, LadderEntry, MatchRecord, User
from sc2scala.server.web import DatabaseSession, render, signed_in_user

router = APIRouter()

_MATCHES_ON_LADDER_PAGE = 20
_MATCHES_PER_PAGE = 50

SignedInUser = Annotated[User | None, Depends(signed_in_user)]


@router.get("/", response_class=HTMLResponse)
def ladders_page(request: Request, session: DatabaseSession, user: SignedInUser) -> HTMLResponse:
    ladders = session.scalars(
        select(Ladder)
        .where(Ladder.retired_at.is_(None))
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
    """The ladder's standings and latest matches, with forms to enter a bot and manage entries; for one who may not
    see a protected ladder, a form to join it."""
    ladder = ladder_named(session, name)
    if ladder is None:
        raise HTTPException(404, f"No ladder is named {name}")
    if ladder.is_protected and not is_member(ladder, user):
        return render(request, "ladder_join.html", user=user, ladder=ladder, status_code=403)
    entered = {entry.bot_id for entry in ladder.entries}
    own_bots = playing_bots_of(session, user)
    return render(
        request,
        "ladder.html",
        user=user,
        ladder=ladder,
        standings=standings(session, ladder),
        matches=_shown_matches(_latest_matches(session, ladder, limit=_MATCHES_ON_LADDER_PAGE)),
        interval_width=INTERVAL_WIDTH,
        prior_sd=PRIOR_SD,
        may_enter=may_enter(ladder, user),
        enterable_bots=[bot for bot in own_bots if bot.latest_version and bot.id not in entered],
        has_bots=bool(own_bots),
        may_withdraw=lambda entry: may_withdraw(entry, user),
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


@router.get("/ladders/{name}/runners", response_class=HTMLResponse)
def runners_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    """The runner machines that played the ladder's matches, and those its owner barred, with forms to bar them."""
    ladder = visible_ladder(session, name, user)
    return render(
        request,
        "ladder_runners.html",
        user=user,
        ladder=ladder,
        runners=runners_of(session, ladder),
        is_owner=user is not None and ladder.owner_id == user.id,
    )


@router.get("/ladders/{name}/pairs", response_class=HTMLResponse)
def pairs_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    ladder = visible_ladder(session, name, user)
    return render(request, "pairs.html", user=user, ladder=ladder, pairs=pair_records(session, ladder))


@router.get("/matches/{match_id}", response_class=HTMLResponse)
def match_page(request: Request, session: DatabaseSession, user: SignedInUser, match_id: int) -> HTMLResponse:
    """The match, with links to the logs the user may read while they are kept."""
    record = visible_match(session, user, match_id)
    bot_logs = (record.player_1_logs_sha256, record.player_2_logs_sha256)
    may_read = [seat for seat in (1, 2) if may_read_bot_logs(record, user, seat=seat)]
    return render(
        request,
        "match.html",
        user=user,
        match=_shown_match(record),
        readable_logs=[seat for seat in may_read if bot_logs[seat - 1] is not None],
        engine_logs=record.engine_logs_sha256 is not None and may_read_engine_logs(record, user),
        reads_logs=bool(may_read) or may_read_engine_logs(record, user),
        keep_logs_days=request.app.state.config.keep_logs_days,
    )


@router.get("/matches/{match_id}/replay")
def replay_download(request: Request, session: DatabaseSession, user: SignedInUser, match_id: int) -> FileResponse:
    return replay_of(request, session, user, match_id)


@router.get("/matches/{match_id}/logs/{part}")
def logs_download(
    request: Request, session: DatabaseSession, user: SignedInUser, match_id: int, part: str
) -> FileResponse:
    return logs_of(request, session, user, match_id, part)


@router.get("/bots", response_class=HTMLResponse)
def bots_page(request: Request, session: DatabaseSession, user: SignedInUser) -> HTMLResponse:
    """Every bot but the retired, by name."""
    bots = session.scalars(
        select(BotRecord)
        .where(BotRecord.retired_at.is_(None))
        .options(selectinload(BotRecord.owner), selectinload(BotRecord.versions))
        .order_by(func.lower(BotRecord.name))
    )
    return render(request, "bots.html", user=user, bots=list(bots))


@router.get("/bots/{name}", response_class=HTMLResponse)
def bot_page(request: Request, session: DatabaseSession, user: SignedInUser, name: str) -> HTMLResponse:
    """The bot's ladders and versions, and its latest matches in every ladder the user may see, those it left too."""
    bot = bot_named(session, name)
    if bot is None:
        raise HTTPException(404, f"No bot is named {name}")
    bot_entries = list(
        session.scalars(select(LadderEntry).join(Ladder).where(LadderEntry.bot_id == bot.id).order_by(Ladder.name))
    )
    ladders = [
        {"ladder": entry.ladder, "entry": entry, "standing": rating_of_latest_version(session, entry.ladder, entry)}
        for entry in bot_entries
        if not entry.ladder.is_protected or is_member(entry.ladder, user)
    ]
    version_ids = [version.id for version in bot.versions]
    played_by_bot = MatchRecord.player_1_version_id.in_(version_ids) | MatchRecord.player_2_version_id.in_(version_ids)
    played_in = session.scalars(
        select(Ladder).where(Ladder.id.in_(select(MatchRecord.ladder_id).where(played_by_bot).distinct()))
    )
    visible_ids = [ladder.id for ladder in played_in if not ladder.is_protected or is_member(ladder, user)]
    matches = session.scalars(
        select(MatchRecord)
        .where(MatchRecord.ladder_id.in_(visible_ids), played_by_bot)
        .order_by(MatchRecord.id.desc())
        .limit(_MATCHES_ON_LADDER_PAGE)
    )
    is_author = user is not None and bot.owner_id == user.id
    may_change = is_author and bot.retired_at is None
    entered_ladders = {entry.ladder_id for entry in bot_entries}
    enterable_ladders = (
        [ladder for ladder in visible_ladders(session, user) if ladder.id not in entered_ladders]
        if may_change and bot.latest_version
        else []
    )
    return render(
        request,
        "bot.html",
        user=user,
        bot=bot,
        ladders=ladders,
        matches=_shown_matches(list(matches)),
        may_change=may_change,
        matches_played=matches_of_bot(session, bot) if may_change else 0,
        enterable_ladders=enterable_ladders,
        races=list(Race),
    )


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
