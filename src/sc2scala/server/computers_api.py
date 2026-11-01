"""The API's computers: a ladder's owner adding the game's built-in computer to the ladder, and pausing, resuming and
removing it."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sc2scala.computer import Build, Computer, Difficulty
from sc2scala.race import Race
from sc2scala.server.bots_api import RemovalOutcome, commit_or_conflict, removal_summary
from sc2scala.server.ladders_api import ladder_details, open_ladder, owned_ladder
from sc2scala.server.models import Ladder, LadderComputer, MatchRecord, User, utc_now
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/ladders")

TokenUser = Annotated[User, Depends(token_user)]


class ComputerRequest(BaseModel):
    race: str
    difficulty: str
    build: str = Build.RANDOM.value


@router.post("/{name}/computers")
def add_computer(session: DatabaseSession, user: TokenUser, name: str, asked: ComputerRequest) -> dict[str, Any]:
    """Adds the computer of a race, as `Race.from_text` reads it, a difficulty and a build, by default the game's own
    pick, to the owner's ladder."""
    return ladder_details(add_computer_to_ladder(session, user, name, computer_asked(asked)), user)


@router.delete("/{name}/computers/{computer_name}")
def remove_computer(session: DatabaseSession, user: TokenUser, name: str, computer_name: str) -> dict[str, Any]:
    """Deletes the computer from the owner's ladder if it never played there, or else retires it."""
    return removal_summary(remove_computer_from_ladder(session, user, name, computer_name))


@router.post("/{name}/computers/{computer_name}/pause")
def pause_computer(session: DatabaseSession, user: TokenUser, name: str, computer_name: str) -> dict[str, Any]:
    return ladder_details(pause_computer_in_ladder(session, user, name, computer_name, paused=True).ladder, user)


@router.post("/{name}/computers/{computer_name}/resume")
def resume_computer(session: DatabaseSession, user: TokenUser, name: str, computer_name: str) -> dict[str, Any]:
    return ladder_details(pause_computer_in_ladder(session, user, name, computer_name, paused=False).ladder, user)


def computer_asked(asked: ComputerRequest) -> Computer:
    """The computer `asked` names; 422 for a race, difficulty or build there is none of."""
    try:
        return Computer(
            race=Race.from_text(asked.race),
            difficulty=Difficulty.from_text(asked.difficulty),
            build=Build.from_text(asked.build),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def add_computer_to_ladder(session: Session, user: User, name: str, computer: Computer) -> Ladder:
    """Adds `computer` to the owner's ladder, or brings it back, its record kept, if it was retired there; 403 for
    another's ladder, and 409 for a retired ladder or one that holds the computer already."""
    ladder = open_ladder(owned_ladder(session, name, user))
    held = next((held for held in ladder.computers if held.computer == computer), None)
    if held is None:
        ladder.computers.append(
            LadderComputer(race=computer.race.value, difficulty=computer.difficulty.value, build=computer.build.value)
        )
    elif held.retired_at is None:
        raise HTTPException(409, f"{ladder.name} holds {computer.name} already")
    else:
        held.retired_at = None
        held.paused = False
    commit_or_conflict(session, f"{computer.name} was added to {ladder.name} at the same moment")
    return ladder


def remove_computer_from_ladder(session: Session, user: User, name: str, computer_name: str) -> RemovalOutcome:
    """Deletes the computer from the owner's ladder if it never played there, or else retires it, so that its matches
    keep their player: it is drawn no more and leaves the standings."""
    held = _computer_of(session, user, name, computer_name)
    # Retired first, which takes the database's write lock, so that no match is drawn against it between the count and
    # the delete, which would delete that match with it.
    held.retired_at = utc_now()
    session.flush()
    matches = (
        session.scalar(select(func.count()).select_from(MatchRecord).where(MatchRecord.player_2_computer_id == held.id))
        or 0
    )
    removal = RemovalOutcome(name=held.name, retired=matches > 0, matches=matches)
    if not removal.retired:
        session.delete(held)
    session.commit()
    return removal


def pause_computer_in_ladder(
    session: Session, user: User, name: str, computer_name: str, *, paused: bool
) -> LadderComputer:
    """Pauses the computer in the owner's ladder, or lets it play again."""
    held = _computer_of(session, user, name, computer_name)
    held.paused = paused
    session.commit()
    return held


def _computer_of(session: Session, user: User, name: str, computer_name: str) -> LadderComputer:
    """The computer of the owner's ladder named `computer_name`, but a retired one; 403 for another's ladder, 409 for a
    retired ladder, and 404 for a computer it does not hold."""
    ladder = open_ladder(owned_ladder(session, name, user))
    try:
        computer = Computer.from_name(computer_name)
    except ValueError:
        computer = None
    held = next(
        (held for held in ladder.computers if held.computer == computer and held.retired_at is None),
        None,
    )
    if held is None:
        raise HTTPException(404, f"{ladder.name} holds no computer {computer_name}")
    return held
