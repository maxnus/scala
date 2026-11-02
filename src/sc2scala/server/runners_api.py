"""The API's runner machines, as their owner registers and changes them with a user's token: what each serves, how many
matches it plays at once, and pausing it; and what each plays now."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.api_tokens import hash_of_token, new_runner_token
from sc2scala.runner_config import MOST_MATCHES_AT_ONCE
from sc2scala.server.bots_api import commit_or_conflict
from sc2scala.server.ladder_runners_api import barred_from
from sc2scala.server.ladders_api import visible_ladder
from sc2scala.server.match_draw import VOID, leased_matches
from sc2scala.server.models import Ladder, MatchRecord, RunnerMachine, User, utc_now
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/runners")

_MACHINE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,39}")
NOT_SEEN_AFTER = timedelta(minutes=5)
"""How long after a machine's last call it shows as not seen: a runner asks at least each minute, even while it plays as
many matches as it may."""

TokenUser = Annotated[User, Depends(token_user)]


class MachineRegistration(BaseModel):
    name: str
    serves_public: bool = True
    ladders: list[str] = []


class MachineChanges(BaseModel):
    serves_public: bool | None = None
    ladders: list[str] | None = None
    matches_at_once: int | None = None
    paused: bool | None = None


@dataclass(frozen=True)
class MachineStatus:
    """What a machine plays now, or why it plays nothing."""

    playing: list[MatchRecord]
    idle_because: str | None
    """Why it plays nothing, as its owner's page says it: never seen, not seen lately, paused, outside its windows, or
    no match to play; `None` while it plays."""


@router.post("")
def register_machine(session: DatabaseSession, user: TokenUser, registration: MachineRegistration) -> dict[str, Any]:
    """Registers a machine of the user's, which serves every public ladder if `serves_public`, and the ladders named.

    Answers with the machine's token, once.
    """
    check_machine_name(registration.name)
    if machine_named(session, user, registration.name) is not None:
        raise HTTPException(409, f"You have a machine named {registration.name} already")
    machine, token = new_machine(
        user,
        registration.name,
        serves_public=registration.serves_public,
        ladders=ladders_named(session, registration.ladders, user),
    )
    session.add(machine)
    commit_or_conflict(session, f"Another machine named {registration.name} was registered at the same moment")
    return {**machine_summary(session, machine), "token": token}


@router.get("")
def my_machines(session: DatabaseSession, user: TokenUser) -> list[dict[str, Any]]:
    machines = session.scalars(
        select(RunnerMachine).where(RunnerMachine.owner_id == user.id).order_by(RunnerMachine.name)
    )
    return [machine_summary(session, machine) for machine in machines]


@router.patch("/{name}")
def change_machine(session: DatabaseSession, user: TokenUser, name: str, changes: MachineChanges) -> dict[str, Any]:
    machine = change_machine_settings(
        session,
        user,
        name,
        serves_public=changes.serves_public,
        ladders=changes.ladders,
        matches_at_once=changes.matches_at_once,
        paused=changes.paused,
    )
    return machine_summary(session, machine)


@router.delete("/{name}")
def remove_machine(session: DatabaseSession, user: TokenUser, name: str) -> dict[str, str]:
    """Removes a machine, whose token then fails, and voids the matches it was playing."""
    remove_machine_named(session, user, name)
    return {"removed": name}


def change_machine_settings(
    session: Session,
    user: User,
    name: str,
    *,
    serves_public: bool | None = None,
    ladders: list[str] | None = None,
    matches_at_once: int | None = None,
    paused: bool | None = None,
) -> RunnerMachine:
    """Changes the user's machine, each setting given: what it serves, how many matches it plays at once, and whether
    it is paused; 404 for no such machine, 404 or 403 for a ladder the user may not see, and 422 for more matches at
    once than it allows.

    Its runner follows at its next ask, within the 30 s an idle runner waits; fewer matches at once, or a pause, lets
    the matches being played end.
    """
    machine = _existing_machine(session, user, name)
    if matches_at_once is not None:
        _check_matches_at_once(machine, matches_at_once)
        machine.matches_at_once = matches_at_once
    if paused is not None:
        machine.paused = paused
    if serves_public is not None:
        machine.serves_public = serves_public
    if ladders is not None:
        machine.ladders = ladders_named(session, ladders, user)
    session.commit()
    return machine


def _check_matches_at_once(machine: RunnerMachine, matches_at_once: int) -> None:
    """Raises 422 unless the machine may play `matches_at_once` at once: from 1 to the most it allows, as its runner
    said last."""
    most = machine.max_matches_at_once or MOST_MATCHES_AT_ONCE
    if 1 <= matches_at_once <= most:
        return
    if machine.max_matches_at_once is None:
        raise HTTPException(422, f"A machine plays 1 to {most} matches at once, not {matches_at_once}")
    raise HTTPException(
        422,
        f"{machine.name} allows 1 to {most} matches at once, not {matches_at_once}: "
        "max_matches_at_once in its runner.toml sets the most",
    )


def check_machine_name(name: str) -> None:
    """Raises 422 unless `name` is a machine's name: letters, digits and '-', 40 at most."""
    if not _MACHINE_NAME_PATTERN.fullmatch(name):
        raise HTTPException(422, f"A machine's name is letters, digits and '-', 40 at most, not {name!r}")


def new_machine(user: User, name: str, *, serves_public: bool, ladders: list[Ladder]) -> tuple[RunnerMachine, str]:
    """A new machine of the user's, not yet in a session, with a new token, which it keeps as its hash; and the
    token."""
    token = new_runner_token()
    machine = RunnerMachine(
        owner_id=user.id, name=name, token_hash=hash_of_token(token), serves_public=serves_public, ladders=ladders
    )
    return machine, token


def remove_machine_named(session: Session, user: User, name: str) -> None:
    """Removes the user's machine, whose token then fails, and voids the matches it was playing; 404 if none."""
    remove_runner_machine(session, _existing_machine(session, user, name))


def remove_runner_machine(session: Session, machine: RunnerMachine) -> None:
    """Removes the machine, whose token then fails, and voids the matches it was playing."""
    void_leased_matches(session, machine, because="its runner machine was removed")
    session.delete(machine)
    session.commit()


def void_leased_matches(session: Session, machine: RunnerMachine, *, because: str) -> None:
    """Voids the matches leased to the machine, which it will not report, `because` it says why, uncommitted."""
    for match in leased_matches(session, machine):
        match.status = VOID
        match.void_because = because
        match.finished_at = utc_now()


def machine_summary(session: Session, machine: RunnerMachine) -> dict[str, Any]:
    status = machine_status(session, machine, now=utc_now())
    return {
        "name": machine.name,
        "owner": machine.owner.login,
        "serves_public": machine.serves_public,
        "ladders": [ladder.name for ladder in machine.ladders],
        "barred_from": barred_from(session, machine),
        "last_seen_at": machine.last_seen_at.isoformat() if machine.last_seen_at else None,
        "matches_at_once": machine.matches_at_once,
        "paused": machine.paused,
        "max_matches_at_once": machine.max_matches_at_once,
        "follows_its_controls": machine.follows_its_controls,
        "scala_version": machine.scala_version,
        "engine": machine.engine,
        "playing": [match.id for match in status.playing],
        "idle_because": status.idle_because,
    }


def machine_status(session: Session, machine: RunnerMachine, *, now: datetime) -> MachineStatus:
    """What the machine plays now, or why it plays nothing.

    A runner of step 9 on asks at least each minute, so one not seen lately plays nothing, whatever it holds until
    their deadline; one of an earlier release asks only when it is free, and plays what it holds.
    """
    seen_lately = machine.last_seen_at is not None and now - machine.last_seen_at <= NOT_SEEN_AFTER
    if seen_lately or not machine.follows_its_controls:
        playing = leased_matches(session, machine)
        if playing:
            return MachineStatus(playing=playing, idle_because=None)
    if machine.last_seen_at is None:
        because = "never seen"
    elif not seen_lately:
        because = f"not seen in the last {NOT_SEEN_AFTER.seconds // 60} minutes"
    elif machine.follows_its_controls and machine.paused:
        because = "paused"
    elif machine.follows_its_controls and machine.next_window_opens:
        opens = datetime.fromisoformat(machine.next_window_opens)
        because = f"outside its windows until {opens:%a %H:%M}, its time"
    else:
        because = "no match to play"
    return MachineStatus(playing=[], idle_because=because)


def ladders_named(session: Session, names: list[str], user: User) -> list[Ladder]:
    """The ladders named, each one the user may see and not retired; 404, 403 or 409 for any other."""
    ladders = [visible_ladder(session, name, user) for name in dict.fromkeys(names)]
    retired = next((ladder for ladder in ladders if ladder.retired_at is not None), None)
    if retired is not None:
        raise HTTPException(409, f"{retired.name} is retired: it draws no more matches")
    return ladders


def machine_named(session: Session, user: User, name: str) -> RunnerMachine | None:
    return session.scalar(select(RunnerMachine).where(RunnerMachine.owner_id == user.id, RunnerMachine.name == name))


def _existing_machine(session: Session, user: User, name: str) -> RunnerMachine:
    machine = machine_named(session, user, name)
    if machine is None:
        raise HTTPException(404, f"You have no machine named {name}")
    return machine
