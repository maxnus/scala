"""The API of the runners that play a ladder's matches: who played them, and the runners its owner barred."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sc2scala.result_code import ResultCode
from sc2scala.server.ladder_ratings import refit_after_result
from sc2scala.server.ladders_api import owned_ladder, visible_ladder
from sc2scala.server.match_draw import FAILED_STARTS, FINISHED, LEASED, VOID
from sc2scala.server.models import BarredRunner, Ladder, MatchRecord, RunnerMachine, User, utc_now
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/ladders")

TokenUser = Annotated[User, Depends(token_user)]


class BarRequest(BaseModel):
    runner: str
    """The machine, as `owner/name`."""
    keep_results: bool = False


@router.get("/{name}/runners")
def ladder_runners(session: DatabaseSession, user: TokenUser, name: str) -> list[dict[str, Any]]:
    """Every runner that played the ladder's matches, or that its owner barred, with its matches by status and its
    latest, the most matches first."""
    ladder = visible_ladder(session, name, user)
    counts: dict[str, dict[str, int]] = {}
    latest: dict[str, datetime] = {}
    for runner, status, count, last_drawn_at in session.execute(
        select(MatchRecord.runner, MatchRecord.status, func.count(), func.max(MatchRecord.drawn_at))
        .where(MatchRecord.ladder_id == ladder.id, MatchRecord.runner.is_not(None))
        .group_by(MatchRecord.runner, MatchRecord.status)
    ):
        if runner is None:
            continue
        counts.setdefault(runner, {FINISHED: 0, VOID: 0, LEASED: 0})[status] = count
        latest[runner] = max(latest.get(runner, last_drawn_at), last_drawn_at)
    barred = {bar.runner: bar for bar in ladder.barred_runners}
    runners = sorted(set(counts) | set(barred), key=lambda runner: (-counts.get(runner, {}).get(FINISHED, 0), runner))
    return [
        {
            "runner": runner,
            "finished": counts.get(runner, {}).get(FINISHED, 0),
            "void": counts.get(runner, {}).get(VOID, 0),
            "being_played": counts.get(runner, {}).get(LEASED, 0),
            "last_drawn_at": _iso_or_none(latest.get(runner)),
            "barred_at": _iso_or_none(barred[runner].barred_at) if runner in barred else None,
        }
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
    ladder = owned_ladder(session, name, user)
    if not _runner_exists(session, ladder, bar.runner):
        raise HTTPException(404, f"No runner machine is named {bar.runner}")
    if any(barred.runner == bar.runner for barred in ladder.barred_runners):
        raise HTTPException(409, f"{bar.runner} is barred from {ladder.name} already")
    now = utc_now()
    ladder.barred_runners.append(BarredRunner(runner=bar.runner, barred_at=now, results_voided=not bar.keep_results))
    statuses = [LEASED] if bar.keep_results else [LEASED, FINISHED]
    voided = list(
        session.scalars(
            select(MatchRecord).where(
                MatchRecord.ladder_id == ladder.id,
                MatchRecord.runner == bar.runner,
                MatchRecord.status.in_(statuses),
            )
        )
    )
    results_voided = [match for match in voided if match.status == FINISHED]
    for match in voided:
        match.void_because = (
            "its runner was barred by the ladder's owner"
            if match.status == FINISHED
            else "its runner was barred by the ladder's owner while it played"
        )
        match.status = VOID
        match.finished_at = match.finished_at or now
    _forget_failed_starts(ladder, results_voided)
    session.commit()
    if results_voided:
        background.add_task(refit_after_result, request.app, ladder.id)
    return {"runner": bar.runner, "barred_at": now.isoformat(), "results_voided": len(results_voided)}


@router.delete("/{name}/barred-runners/{owner}/{machine}")
def unbar_runner(session: DatabaseSession, user: TokenUser, name: str, owner: str, machine: str) -> dict[str, Any]:
    """Lets a barred runner play the ladder's matches again, for the ladder's owner alone; what it voided stays void."""
    ladder = owned_ladder(session, name, user)
    runner = f"{owner}/{machine}"
    bar = next((barred for barred in ladder.barred_runners if barred.runner == runner), None)
    if bar is None:
        raise HTTPException(404, f"{runner} is not barred from {ladder.name}")
    ladder.barred_runners.remove(bar)
    session.commit()
    return {"runner": runner, "barred": False}


def barred_from(session: Session, machine: RunnerMachine) -> list[str]:
    """The names of the ladders that barred `machine`."""
    return list(
        session.scalars(
            select(Ladder.name)
            .join(BarredRunner)
            .where(BarredRunner.runner == machine.runner_name)
            .order_by(Ladder.name)
        )
    )


def _runner_exists(session: Session, ladder: Ladder, runner: str) -> bool:
    """Whether `runner`, `owner/name`, played a match of the ladder, or is a machine registered now."""
    played = session.scalar(
        select(MatchRecord.id).where(MatchRecord.ladder_id == ladder.id, MatchRecord.runner == runner).limit(1)
    )
    if played is not None:
        return True
    owner, _, machine = runner.partition("/")
    return (
        session.scalar(
            select(RunnerMachine.id).join(User).where(User.login == owner, RunnerMachine.name == machine).limit(1)
        )
        is not None
    )


def _forget_failed_starts(ladder: Ladder, results_voided: list[MatchRecord]) -> None:
    """Begins anew the count of failed starts of each entry a voided `InitializationError` counted against, and lets
    it play again if those failed starts paused it."""
    bot_ids = {
        version.bot_id
        for match in results_voided
        if match.result == ResultCode.INITIALIZATION_ERROR.value
        for version in match.versions
    }
    for entry in ladder.entries:
        if entry.bot_id in bot_ids:
            entry.failed_starts_in_a_row = 0
            if entry.paused and (entry.paused_because or "").startswith(FAILED_STARTS):
                entry.paused = False
                entry.paused_because = None


def _iso_or_none(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
