"""The API of the runners that play a ladder's matches: who played them, and the runners its owner barred.

A runner is named `owner/name`, by its owner's login now, but kept by its owner's account and its name, so that a bar
holds whatever the owner's login becomes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.orm import Session

from sc2scala.result_code import ResultCode
from sc2scala.server.bots_api import commit_or_conflict
from sc2scala.server.ladder_ratings import refit_after_result
from sc2scala.server.ladders_api import owned_ladder, visible_ladder
from sc2scala.server.match_draw import FAILED_STARTS, FINISHED, LEASED, VOID
from sc2scala.server.models import BarredRunner, BotVersion, Ladder, MatchRecord, RunnerMachine, User, utc_now
from sc2scala.server.sign_in_routes import user_named
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/ladders")

TokenUser = Annotated[User, Depends(token_user)]


class BarRequest(BaseModel):
    runner: str
    """The machine, as `owner/name`."""
    keep_results: bool = False


@router.get("/{name}/runners")
def ladder_runners(session: DatabaseSession, user: TokenUser, name: str) -> list[dict[str, Any]]:
    """Every runner that played the ladder's matches, or that its owner barred, with its matches by status, its
    latest, and its bar, the most matches first."""
    return [
        {
            "runner": record.runner,
            "finished": record.finished,
            "void": record.void,
            "being_played": record.being_played,
            "last_drawn_at": record.last_drawn_at.isoformat() if record.last_drawn_at else None,
            "barred_at": record.bar.barred_at.isoformat() if record.bar else None,
            "results_voided": record.bar.results_voided if record.bar else None,
        }
        for record in runners_of(session, visible_ladder(session, name, user))
    ]


@dataclass(frozen=True)
class RunnerRecord:
    """A runner's matches in a ladder, by status, its latest draw there, and its bar there, if any."""

    runner: str
    finished: int
    void: int
    being_played: int
    last_drawn_at: datetime | None
    bar: BarredRunner | None

    @property
    def owner_and_machine(self) -> tuple[str, str]:
        owner, _, machine = self.runner.partition("/")
        return owner, machine


def runners_of(session: Session, ladder: Ladder) -> list[RunnerRecord]:
    """Every runner that played the ladder's matches, or that its owner barred, the most finished matches first."""
    counts: dict[str, dict[str, int]] = {}
    latest: dict[str, datetime] = {}
    for login, machine_name, status, count, last_drawn_at in session.execute(
        select(
            User.login,
            MatchRecord.runner_machine_name,
            MatchRecord.status,
            func.count(),
            func.max(MatchRecord.drawn_at),
        )
        .join(User, User.id == MatchRecord.runner_owner_id)
        .where(MatchRecord.ladder_id == ladder.id)
        .group_by(User.login, MatchRecord.runner_machine_name, MatchRecord.status)
    ):
        runner = f"{login}/{machine_name}"
        counts.setdefault(runner, {FINISHED: 0, VOID: 0, LEASED: 0})[status] = count
        latest[runner] = max(latest.get(runner, last_drawn_at), last_drawn_at)
    bars = {bar.runner_name: bar for bar in ladder.barred_runners}
    no_matches = {FINISHED: 0, VOID: 0, LEASED: 0}
    runners = sorted(set(counts) | set(bars), key=lambda runner: (-counts.get(runner, no_matches)[FINISHED], runner))
    return [
        RunnerRecord(
            runner=runner,
            finished=counts.get(runner, no_matches)[FINISHED],
            void=counts.get(runner, no_matches)[VOID],
            being_played=counts.get(runner, no_matches)[LEASED],
            last_drawn_at=latest.get(runner),
            bar=bars.get(runner),
        )
        for runner in runners
    ]


@router.post("/{name}/barred-runners")
def bar_runner(
    request: Request, background: BackgroundTasks, session: DatabaseSession, user: TokenUser, name: str, bar: BarRequest
) -> dict[str, Any]:
    """Bars a runner from the ladder's matches, for the ladder's owner alone, and voids the matches it is playing there.

    Unless `keep_results`, voids its results there too, refits the ratings without them, and lets the entries its
    failed starts paused play again.
    """
    barred = bar_runner_named(session, user, name, bar.runner, keep_results=bar.keep_results)
    if barred.results_voided:
        background.add_task(refit_after_result, request.app, barred.ladder_id)
    return {"runner": barred.runner, "barred_at": barred.barred_at.isoformat(), "results_voided": barred.results_voided}


@dataclass(frozen=True)
class Bar:
    """What barring a runner did: the runner, by its owner's login now, when, how many results it voided, and in
    which ladder, whose ratings need refitting if any."""

    runner: str
    barred_at: datetime
    results_voided: int
    ladder_id: int


def bar_runner_named(session: Session, user: User, name: str, runner_name: str, *, keep_results: bool) -> Bar:
    """Bars the runner `runner_name`, `owner/name`, from the owner's ladder, as `bar_runner` says, but for refitting
    the ratings, which is the caller's; 404 for no such runner, 409 if it is barred already."""
    ladder = owned_ladder(session, name, user)
    owner_login, _, machine_name = runner_name.partition("/")
    owner = user_named(session, owner_login)
    if owner is None or not _runner_exists(session, ladder, owner, machine_name):
        raise HTTPException(404, f"No runner machine is named {runner_name}")
    runner = f"{owner.login}/{machine_name}"
    if _bar_of(ladder, owner, machine_name) is not None:
        raise HTTPException(409, f"{runner} is barred from {ladder.name} already")
    now = utc_now()
    ladder.barred_runners.append(
        BarredRunner(owner=owner, machine_name=machine_name, barred_at=now, results_voided=not keep_results)
    )
    played_there = (
        MatchRecord.ladder_id == ladder.id,
        MatchRecord.runner_owner_id == owner.id,
        MatchRecord.runner_machine_name == machine_name,
    )
    results_voided = 0
    if not keep_results:
        finished = (*played_there, MatchRecord.status == FINISHED)
        results_voided = session.scalar(select(func.count()).select_from(MatchRecord).where(*finished)) or 0
        _forget_failed_starts(session, ladder, finished)
        _void(session, finished, because="its runner was barred by the ladder's owner", now=None)
    being_played = (*played_there, MatchRecord.status == LEASED)
    _void(session, being_played, because="its runner was barred by the ladder's owner while it played", now=now)
    commit_or_conflict(session, f"{runner} was barred from {ladder.name} at the same moment")
    return Bar(runner=runner, barred_at=now, results_voided=results_voided, ladder_id=ladder.id)


@router.delete("/{name}/barred-runners/{owner}/{machine}")
def unbar_runner(session: DatabaseSession, user: TokenUser, name: str, owner: str, machine: str) -> dict[str, Any]:
    """Lets a barred runner play the ladder's matches again, for the ladder's owner alone; what it voided stays void."""
    return {"runner": unbar_runner_named(session, user, name, owner, machine), "barred": False}


def unbar_runner_named(session: Session, user: User, name: str, owner: str, machine: str) -> str:
    """Lifts the bar of the runner `owner/machine` from the owner's ladder, and returns its name; 404 if not barred."""
    ladder = owned_ladder(session, name, user)
    owner_user = user_named(session, owner)
    bar = _bar_of(ladder, owner_user, machine) if owner_user is not None else None
    if bar is None:
        raise HTTPException(404, f"{owner}/{machine} is not barred from {ladder.name}")
    runner = bar.runner_name
    ladder.barred_runners.remove(bar)
    session.commit()
    return runner


def barred_from(session: Session, machine: RunnerMachine) -> list[str]:
    """The names of the ladders that barred `machine`."""
    return list(
        session.scalars(
            select(Ladder.name)
            .join(BarredRunner)
            .where(BarredRunner.owner_id == machine.owner_id, BarredRunner.machine_name == machine.name)
            .order_by(Ladder.name)
        )
    )


def _bar_of(ladder: Ladder, owner: User, machine_name: str) -> BarredRunner | None:
    return next(
        (bar for bar in ladder.barred_runners if bar.owner_id == owner.id and bar.machine_name == machine_name), None
    )


def _runner_exists(session: Session, ladder: Ladder, owner: User, machine_name: str) -> bool:
    """Whether the owner's machine of that name played a match of the ladder, or is registered now."""
    played = select(MatchRecord.id).where(
        MatchRecord.ladder_id == ladder.id,
        MatchRecord.runner_owner_id == owner.id,
        MatchRecord.runner_machine_name == machine_name,
    )
    registered = select(RunnerMachine.id).where(RunnerMachine.owner_id == owner.id, RunnerMachine.name == machine_name)
    return any(session.scalar(query.limit(1)) is not None for query in (played, registered))


def _void(session: Session, conditions: tuple[ColumnElement[bool], ...], *, because: str, now: datetime | None) -> None:
    """Voids the matches that meet `conditions`, `because`; those not finished yet end `now`."""
    values: dict[str, Any] = {"status": VOID, "void_because": because}
    if now is not None:
        values["finished_at"] = now
    session.execute(update(MatchRecord).where(*conditions).values(**values))


def _forget_failed_starts(session: Session, ladder: Ladder, conditions: tuple[ColumnElement[bool], ...]) -> None:
    """Begins anew the count of failed starts of each entry that an `InitializationError` among the matches meeting
    `conditions` counted against, and lets it play again if its failed starts paused it."""
    bot_ids = set(
        session.scalars(
            select(BotVersion.bot_id)
            .join(
                MatchRecord,
                or_(MatchRecord.player_1_version_id == BotVersion.id, MatchRecord.player_2_version_id == BotVersion.id),
            )
            .where(*conditions, MatchRecord.result == ResultCode.INITIALIZATION_ERROR.value)
        )
    )
    for entry in ladder.entries:
        if entry.bot_id in bot_ids:
            entry.failed_starts_in_a_row = 0
            if entry.paused and (entry.paused_because or "").startswith(FAILED_STARTS):
                entry.paused = False
                entry.paused_because = None
