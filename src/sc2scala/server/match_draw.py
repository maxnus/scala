"""Drawing a match for a runner, voiding matches past their deadline or lost by their runner, and recording a match's
result."""

import random
from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from sc2scala.game_limits import GameLimits
from sc2scala.result_code import ResultCode
from sc2scala.server.models import (
    BotRecord,
    BotVersion,
    Ladder,
    LadderComputer,
    LadderEntry,
    MatchRecord,
    RunnerMachine,
)

LEASED = "leased"
FINISHED = "finished"
VOID = "void"
MAX_FAILED_STARTS_IN_A_ROW = 5
FAILED_STARTS = "failed to start"
"""How an entry paused for its failed starts says so, at its start."""

_RESULTS_SHOWING_BOTH_STARTED = frozenset(ResultCode) - {ResultCode.INITIALIZATION_ERROR, ResultCode.ERROR}


def draw_match(
    session: Session,
    machine: RunnerMachine,
    *,
    rng: random.Random,
    now: datetime,
    lease_margin: timedelta,
    plays_computers: bool,
) -> MatchRecord | None:
    """A match for `machine`, leased to it until the ladder's real-time limit and `lease_margin` have passed, or `None`
    if no ladder it serves has a pair that may play.

    A ladder's pairs are any two of its active bots, and, if the machine `plays_computers`, any of its active bots with
    any of its active computers, but never two computers. The ladder is drawn among those with a pair, each weighted
    by its active bots over the bots one of its matches seats on average, so that a bot gets about as many games
    whichever ladder it is in; the pair uniformly among its pairs, two bots in random seats, and a computer always in
    seat 2, as the game numbers it; and the map uniformly from its pool.
    """
    candidates = []
    for ladder in served_ladders(session, machine):
        bots = _active(ladder)
        computers = _active_computers(ladder) if plays_computers else []
        if ladder.maps and _bot_pairs(len(bots)) + len(bots) * len(computers):
            candidates.append((ladder, bots, computers))
    if not candidates:
        return None
    weights = [_weight_of_ladder(len(bots), len(computers)) for _, bots, computers in candidates]
    ladder, bots, computers = rng.choices(candidates, weights=weights)[0]
    bot_pairs = _bot_pairs(len(bots))
    opponent: LadderEntry | LadderComputer
    if computers and rng.randrange(bot_pairs + len(bots) * len(computers)) >= bot_pairs:
        entry, opponent = rng.choice(bots), rng.choice(computers)
    else:
        entry, opponent = rng.sample(bots, 2)
    real_time_limit = timedelta(seconds=GameLimits(**ladder.game_limits).max_real_time_seconds)
    match = MatchRecord(
        ladder=ladder,
        map_file=rng.choice(ladder.maps),
        player_1_version=_latest_version(entry),
        player_1_race=entry.bot.race,
        machine=machine,
        runner_owner_id=machine.owner_id,
        runner_machine_name=machine.name,
        status=LEASED,
        drawn_at=now,
        deadline=now + real_time_limit + lease_margin,
    )
    if isinstance(opponent, LadderComputer):
        match.player_2_computer, match.player_2_race = opponent, opponent.race
    else:
        match.player_2_version, match.player_2_race = _latest_version(opponent), opponent.bot.race
    session.add(match)
    return match


def served_ladders(session: Session, machine: RunnerMachine) -> list[Ladder]:
    """The ladders `machine` serves: every public one if it serves them, and those it lists, a protected one while its
    owner is a member; none that barred it, or is retired."""
    ladders = session.scalars(
        select(Ladder)
        .where(Ladder.retired_at.is_(None))
        .options(
            selectinload(Ladder.maps),
            selectinload(Ladder.members),
            selectinload(Ladder.barred_runners),
            selectinload(Ladder.entries).selectinload(LadderEntry.bot).selectinload(BotRecord.versions),
            selectinload(Ladder.computers),
        )
    ).all()
    listed = {ladder.id for ladder in machine.ladders}
    served = []
    for ladder in ladders:
        if any(barred.bars(machine) for barred in ladder.barred_runners):
            continue
        owner_is_member = any(member.user_id == machine.owner_id for member in ladder.members)
        if ladder.is_protected:
            if ladder.id in listed and owner_is_member:
                served.append(ladder)
        elif machine.serves_public or ladder.id in listed:
            served.append(ladder)
    return served


def void_expired_leases(session: Session, *, now: datetime) -> None:
    """Voids every leased match whose deadline has passed."""
    session.execute(
        update(MatchRecord)
        .where(MatchRecord.status == LEASED, MatchRecord.deadline < now)
        .values(status=VOID, void_because="no result by the deadline", finished_at=now)
    )


def void_leases_not_played(
    session: Session, machine: RunnerMachine, *, playing: Collection[int], now: datetime
) -> None:
    """Voids the matches leased to `machine` that its runner, asking for another at `now`, does not name among those it
    plays: it lost them, killed in a match say.

    Only the leases drawn by the time the server answered the runner's previous ask go, and only if that answer went
    out before this ask came, so that a match whose answer may still have been on its way when the runner asked
    stays; with no previous ask, every lease was drawn for a runner of an earlier release, which no longer runs.
    """
    answered = machine.last_answered_at
    if answered is not None and answered >= now:
        return
    lost = [MatchRecord.machine_id == machine.id, MatchRecord.status == LEASED, MatchRecord.id.not_in(playing)]
    if answered is not None:
        lost.append(MatchRecord.drawn_at <= answered)
    session.execute(
        update(MatchRecord)
        .where(*lost)
        .values(status=VOID, void_because="its runner no longer played it", finished_at=now)
    )


def leased_matches(session: Session, machine: RunnerMachine) -> list[MatchRecord]:
    """The matches leased to `machine`, oldest first."""
    query = select(MatchRecord).where(MatchRecord.machine_id == machine.id, MatchRecord.status == LEASED)
    return list(session.scalars(query.order_by(MatchRecord.id)))


def plays_fewer_than_it_may(session: Session, machine: RunnerMachine) -> bool:
    """Whether `machine` holds fewer leases than the matches it may play at once."""
    leased = session.scalar(
        select(func.count()).where(MatchRecord.machine_id == machine.id, MatchRecord.status == LEASED)
    )
    return (leased or 0) < machine.matches_it_may_play_at_once


def record_result(session: Session, match: MatchRecord, code: ResultCode, *, now: datetime) -> None:
    """Finishes `match` with `code`, and counts its bots' failed starts in a row.

    `InitializationError` does not say which bot failed to start, so it counts for both, and for the bot alone against
    the computer, which never fails to start; an entry that reaches `MAX_FAILED_STARTS_IN_A_ROW` is paused. A result
    that shows both players started forgets the count.
    """
    match.status = FINISHED
    match.result = code.value
    match.finished_at = now
    for version in match.bot_versions:
        entry = session.scalar(
            select(LadderEntry).where(LadderEntry.ladder_id == match.ladder_id, LadderEntry.bot_id == version.bot_id)
        )
        if entry is None:
            continue
        if code == ResultCode.INITIALIZATION_ERROR:
            entry.failed_starts_in_a_row += 1
            if entry.failed_starts_in_a_row >= MAX_FAILED_STARTS_IN_A_ROW and not entry.paused:
                entry.paused = True
                entry.paused_because = f"{FAILED_STARTS} {entry.failed_starts_in_a_row} times in a row"
        elif code in _RESULTS_SHOWING_BOTH_STARTED:
            entry.failed_starts_in_a_row = 0


def _active(ladder: Ladder) -> list[LadderEntry]:
    return [entry for entry in ladder.entries if not entry.paused and entry.bot.latest_version is not None]


def _latest_version(entry: LadderEntry) -> BotVersion:
    version = entry.bot.latest_version
    if version is None:
        raise RuntimeError("An active entry's bot has no version")
    return version


def _active_computers(ladder: Ladder) -> list[LadderComputer]:
    return [computer for computer in ladder.computers if not computer.paused and computer.retired_at is None]


def _bot_pairs(bots: int) -> int:
    return bots * (bots - 1) // 2


def _weight_of_ladder(bots: int, computers: int) -> float:
    """A ladder's weight in the draw: its active bots over the bots one of its matches seats on average, 2 for a pair
    of bots and 1 for a bot and a computer, which for a ladder of bots alone is half its bots."""
    bot_pairs = _bot_pairs(bots)
    computer_pairs = bots * computers
    return bots * (bot_pairs + computer_pairs) / (2 * bot_pairs + computer_pairs)
