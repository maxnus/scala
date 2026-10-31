"""The API's runner machines, as their owner registers and changes them with a user's token."""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.api_tokens import hash_of_token, new_runner_token
from sc2scala.server.bots_api import commit_or_conflict
from sc2scala.server.ladder_runners_api import barred_from
from sc2scala.server.ladders_api import visible_ladder
from sc2scala.server.match_draw import LEASED, VOID
from sc2scala.server.models import Ladder, MatchRecord, RunnerMachine, User, utc_now
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/runners")

MACHINE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,39}")

TokenUser = Annotated[User, Depends(token_user)]


class MachineRegistration(BaseModel):
    name: str
    serves_public: bool = True
    ladders: list[str] = []


class MachineChanges(BaseModel):
    serves_public: bool | None = None
    ladders: list[str] | None = None


@router.post("")
def register_machine(session: DatabaseSession, user: TokenUser, registration: MachineRegistration) -> dict[str, Any]:
    """Registers a machine of the user's, which serves every public ladder if `serves_public`, and the ladders named.

    Answers with the machine's token, once.
    """
    if not MACHINE_NAME_PATTERN.fullmatch(registration.name):
        raise HTTPException(422, f"A machine's name is letters, digits and '-', 40 at most, not {registration.name!r}")
    if machine_named(session, user, registration.name) is not None:
        raise HTTPException(409, f"You have a machine named {registration.name} already")
    token = new_runner_token()
    machine = RunnerMachine(
        owner_id=user.id,
        name=registration.name,
        token_hash=hash_of_token(token),
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
    machine = change_machine_settings(session, user, name, serves_public=changes.serves_public, ladders=changes.ladders)
    return machine_summary(session, machine)


@router.delete("/{name}")
def remove_machine(session: DatabaseSession, user: TokenUser, name: str) -> dict[str, str]:
    """Removes a machine, whose token then fails, and voids the matches it was playing."""
    remove_machine_named(session, user, name)
    return {"removed": name}


def change_machine_settings(
    session: Session, user: User, name: str, *, serves_public: bool | None, ladders: list[str] | None
) -> RunnerMachine:
    """Changes what the user's machine serves, each setting given; 404 for no such machine, and 404 or 403 for a
    ladder the user may not see."""
    machine = _existing_machine(session, user, name)
    if serves_public is not None:
        machine.serves_public = serves_public
    if ladders is not None:
        machine.ladders = ladders_named(session, ladders, user)
    session.commit()
    return machine


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
    for match in session.scalars(
        select(MatchRecord).where(MatchRecord.machine_id == machine.id, MatchRecord.status == LEASED)
    ):
        match.status = VOID
        match.void_because = because
        match.finished_at = utc_now()


def machine_summary(session: Session, machine: RunnerMachine) -> dict[str, Any]:
    return {
        "name": machine.name,
        "owner": machine.owner.login,
        "serves_public": machine.serves_public,
        "ladders": [ladder.name for ladder in machine.ladders],
        "barred_from": barred_from(session, machine),
        "last_seen_at": machine.last_seen_at.isoformat() if machine.last_seen_at else None,
    }


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
