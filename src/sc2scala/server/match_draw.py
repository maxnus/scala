"""Drawing a match for a runner, voiding matches past their deadline, and recording a match's result."""

import random
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from sc2scala.game_limits import GameLimits
from sc2scala.result_code import ResultCode
from sc2scala.server.models import Ladder, LadderEntry, MatchRecord, RunnerMachine

LEASED = "leased"
FINISHED = "finished"
VOID = "void"
MAX_FAILED_STARTS_IN_A_ROW = 5

_RESULTS_SHOWING_BOTH_STARTED = frozenset(ResultCode) - {ResultCode.INITIALIZATION_ERROR, ResultCode.ERROR}


def draw_match(
    session: Session, machine: RunnerMachine, *, rng: random.Random, now: datetime, lease_margin: timedelta
) -> MatchRecord | None:
    """A match for `machine`, leased to it until the ladder's real-time limit and `lease_margin` have passed, or `None`
    if no ladder it serves has two bots that may play.

    The ladder is drawn among those with two active bots or more, each weighted by its number of active bots; the two
    bots uniformly among its active ones, in random seats; and the map uniformly from its pool.
    """
    candidates = [
        (ladder, entries) for ladder in served_ladders(session, machine) if len(entries := _active(ladder)) >= 2
    ]
    candidates = [(ladder, entries) for ladder, entries in candidates if ladder.maps]
    if not candidates:
        return None
    ladder, entries = rng.choices(candidates, weights=[len(entries) for _, entries in candidates])[0]
    player_1, player_2 = rng.sample(entries, 2)
    version_1 = player_1.bot.latest_version
    version_2 = player_2.bot.latest_version
    if version_1 is None or version_2 is None:
        raise RuntimeError("An active entry's bot has no version")
    real_time_limit = timedelta(seconds=GameLimits(**ladder.game_limits).max_real_time_seconds)
    match = MatchRecord(
        ladder=ladder,
        map_file=rng.choice(ladder.maps),
        player_1_version=version_1,
        player_2_version=version_2,
        player_1_race=player_1.bot.race,
        player_2_race=player_2.bot.race,
        machine=machine,
        status=LEASED,
        drawn_at=now,
        deadline=now + real_time_limit + lease_margin,
    )
    session.add(match)
    return match


def served_ladders(session: Session, machine: RunnerMachine) -> list[Ladder]:
    """The ladders `machine` serves: every public one if it serves them, and those it lists, a protected one while its
    owner is a member."""
    ladders = session.scalars(
        select(Ladder).options(selectinload(Ladder.maps), selectinload(Ladder.members), selectinload(Ladder.entries))
    ).all()
    listed = {ladder.id for ladder in machine.ladders}
    served = []
    for ladder in ladders:
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


def record_result(session: Session, match: MatchRecord, code: ResultCode, *, now: datetime) -> None:
    """Finishes `match` with `code`, and counts its bots' failed starts in a row.

    `InitializationError` does not say which bot failed to start, so it counts for both; an entry that reaches
    `MAX_FAILED_STARTS_IN_A_ROW` is paused. A result that shows both bots started forgets the count.
    """
    match.status = FINISHED
    match.result = code.value
    match.finished_at = now
    for version in match.versions:
        entry = session.scalar(
            select(LadderEntry).where(LadderEntry.ladder_id == match.ladder_id, LadderEntry.bot_id == version.bot_id)
        )
        if entry is None:
            continue
        if code == ResultCode.INITIALIZATION_ERROR:
            entry.failed_starts_in_a_row += 1
            if entry.failed_starts_in_a_row >= MAX_FAILED_STARTS_IN_A_ROW and not entry.paused:
                entry.paused = True
                entry.paused_because = f"failed to start {entry.failed_starts_in_a_row} times in a row"
        elif code in _RESULTS_SHOWING_BOTH_STARTED:
            entry.failed_starts_in_a_row = 0


def _active(ladder: Ladder) -> list[LadderEntry]:
    return [entry for entry in ladder.entries if not entry.paused and entry.bot.latest_version is not None]
