"""The API's ladders: making, changing and removing them, their maps, joining them, and entering bots in them."""

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from sc2scala.errors import UploadRefusedError
from sc2scala.server.bots_api import RemovalOutcome, bot_named, commit_or_conflict, removal_summary
from sc2scala.server.file_store import FileStore
from sc2scala.server.ladder_limits import limits_changed
from sc2scala.server.ladder_ratings import Standing, standings
from sc2scala.server.models import (
    Ladder,
    LadderEntry,
    LadderMember,
    MapFile,
    MatchRecord,
    User,
    runner_machine_ladders,
    utc_now,
)
from sc2scala.server.passwords import hash_of_password, password_matches
from sc2scala.server.upload_checks import MAP_SUFFIX, check_map, map_name_of
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/ladders")

MAP_FILES = "maps"
_LADDER_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,39}")
_NAMES_OF_PAGES = frozenset({"new"})
"""Names a ladder may not take, since `/ladders/<name>` is a page of its own."""
_MEBIBYTE = 1024 * 1024

TokenUser = Annotated[User, Depends(token_user)]


class JoinRequest(BaseModel):
    password: str = ""


class EntryRequest(BaseModel):
    bot: str


class LadderChanges(BaseModel):
    new_password: str | None = None
    remove_password: bool = False
    limits: dict[str, Any] = {}


@router.post("")
def create_ladder(
    request: Request,
    session: DatabaseSession,
    user: TokenUser,
    name: Annotated[str, Form()],
    maps: list[UploadFile],
    password: Annotated[str | None, Form()] = None,
    limits: Annotated[str, Form()] = "{}",
) -> dict[str, Any]:
    """Makes a ladder of the maps uploaded, protected by `password` if given, with `limits`, a JSON object, changed
    from the defaults."""
    ladder = make_ladder(request, session, user, name, maps, password=password, limit_changes=_json_object(limits))
    return ladder_details(ladder, user)


def make_ladder(
    request: Request,
    session: Session,
    user: User,
    name: str,
    maps: list[UploadFile],
    *,
    password: str | None,
    limit_changes: dict[str, Any],
) -> Ladder:
    """Makes the user's ladder of `maps`, protected by `password` if given, with the defaults' limits but for
    `limit_changes`.

    Raises `HTTPException` for a name that is no ladder's, is taken or is kept for a page, or for no map; and
    `UploadRefusedError` for a limit or a map that does not fit.
    """
    if not _LADDER_NAME_PATTERN.fullmatch(name):
        raise HTTPException(422, f"A ladder's name is letters, digits and '-', 40 at most, not {name!r}")
    if name.lower() in _NAMES_OF_PAGES:
        raise HTTPException(422, f"{name!r} names the page that makes ladders; choose another name")
    if ladder_named(session, name) is not None:
        raise HTTPException(409, f"A ladder is named {name} already")
    if not maps:
        raise HTTPException(422, "A ladder needs a map")
    game_limits, resource_limits = limits_changed(limit_changes, game_limits={}, resource_limits={})
    ladder = Ladder(
        name=name,
        owner_id=user.id,
        password_hash=hash_of_password(password) if password else None,
        game_limits=game_limits,
        resource_limits=resource_limits,
    )
    session.add(ladder)
    ladder.members.append(LadderMember(user_id=user.id))
    for upload in maps:
        _add_map(request, session, ladder, upload, uploader=user)
    commit_or_conflict(session, f"Another ladder named {name} was made at the same moment")
    return ladder


@router.get("")
def all_ladders(session: DatabaseSession, user: TokenUser) -> list[dict[str, Any]]:
    """Every ladder but the retired, a protected one shown in full to its members alone."""
    ladders = session.scalars(
        select(Ladder)
        .where(Ladder.retired_at.is_(None))
        .options(
            selectinload(Ladder.owner),
            selectinload(Ladder.maps),
            selectinload(Ladder.entries),
            selectinload(Ladder.members),
        )
        .order_by(Ladder.name)
    )
    return [ladder_summary(ladder, user) for ladder in ladders]


@router.get("/{name}")
def ladder(session: DatabaseSession, user: TokenUser, name: str) -> dict[str, Any]:
    return ladder_details(visible_ladder(session, name, user), user)


@router.patch("/{name}")
def change_ladder(session: DatabaseSession, user: TokenUser, name: str, changes: LadderChanges) -> dict[str, Any]:
    """Sets or removes the password, and changes limits, which the next matches take; for the ladder's owner alone.

    Whoever joined with an earlier password stays a member.
    """
    ladder = change_ladder_settings(
        session,
        user,
        name,
        new_password=changes.new_password,
        remove_password=changes.remove_password,
        limit_changes=changes.limits,
    )
    return ladder_details(ladder, user)


@router.delete("/{name}")
def remove_ladder(session: DatabaseSession, user: TokenUser, name: str) -> dict[str, Any]:
    """Deletes the user's ladder if it never drew a match, or else retires it, as `remove_ladder_named` says."""
    return removal_summary(remove_ladder_named(session, user, name))


@router.post("/{name}/maps")
def add_map(
    request: Request, session: DatabaseSession, user: TokenUser, name: str, map_file: UploadFile
) -> dict[str, Any]:
    return ladder_details(add_maps(request, session, user, name, [map_file]), user)


@router.delete("/{name}/maps/{map_name}")
def remove_map(session: DatabaseSession, user: TokenUser, name: str, map_name: str) -> dict[str, Any]:
    return ladder_details(remove_map_named(session, user, name, map_name), user)


def change_ladder_settings(
    session: Session,
    user: User,
    name: str,
    *,
    new_password: str | None = None,
    remove_password: bool = False,
    limit_changes: dict[str, Any] | None = None,
) -> Ladder:
    """Sets or removes the owner's ladder's password, and changes its limits, which the next matches take.

    Raises `HTTPException` for another's ladder, or for setting and removing the password at once, and
    `LimitRefusedError` for a limit that does not fit. Whoever joined with an earlier password stays a member.
    """
    ladder = owned_ladder(session, name, user)
    if new_password and remove_password:
        raise HTTPException(422, "Set a password or remove it, not both")
    if new_password:
        ladder.password_hash = hash_of_password(new_password)
    if remove_password:
        ladder.password_hash = None
    ladder.game_limits, ladder.resource_limits = limits_changed(
        limit_changes or {}, game_limits=ladder.game_limits, resource_limits=ladder.resource_limits
    )
    session.commit()
    return ladder


def remove_ladder_named(session: Session, user: User, name: str) -> RemovalOutcome:
    """Deletes the owner's ladder, its entries, members and bars with it, if it never drew a match, and frees its name;
    or else retires it: it draws no more matches and takes no more entries, no machine serves it, and it keeps its
    standings as they ended, its matches and its name. A match being played still counts once reported.

    Raises `HTTPException`: 404 for no such ladder, 403 for another's, and 409 for one retired already.
    """
    ladder = owned_ladder(session, name, user)
    if ladder.retired_at is not None:
        raise HTTPException(409, f"{ladder.name} is retired already")
    # Retired first, which takes the database's write lock, so that no match is drawn in it between the count and the
    # delete, which would delete that match with it.
    ladder.retired_at = utc_now()
    session.flush()
    matches = matches_in_ladder(session, ladder)
    removal = RemovalOutcome(name=ladder.name, retired=matches > 0, matches=matches)
    if removal.retired:
        session.execute(delete(runner_machine_ladders).where(runner_machine_ladders.c.ladder_id == ladder.id))
    else:
        session.delete(ladder)
    session.commit()
    return removal


def matches_in_ladder(session: Session, ladder: Ladder) -> int:
    """How many matches the ladder drew, void ones too."""
    return session.scalar(select(func.count()).select_from(MatchRecord).where(MatchRecord.ladder_id == ladder.id)) or 0


def add_maps(request: Request, session: Session, user: User, name: str, uploads: list[UploadFile]) -> Ladder:
    """Adds the uploaded maps to the owner's ladder, none if one is refused: `HTTPException` for another's ladder or a
    map of a name it holds another of, `UploadRefusedError` for a file that is no map."""
    ladder = owned_ladder(session, name, user)
    for upload in uploads:
        _add_map(request, session, ladder, upload, uploader=user)
    session.commit()
    return ladder


def remove_map_named(session: Session, user: User, name: str, map_name: str) -> Ladder:
    """Takes a map out of the owner's ladder; 404 if it holds none of that name, 409 for its last."""
    ladder = owned_ladder(session, name, user)
    held = next((map_file for map_file in ladder.maps if map_file.name == map_name), None)
    if held is None:
        raise HTTPException(404, f"{ladder.name} has no map {map_name}")
    if len(ladder.maps) == 1:
        raise HTTPException(409, f"{map_name} is {ladder.name}'s last map")
    ladder.maps.remove(held)
    session.commit()
    return ladder


@router.post("/{name}/join")
def join_ladder(session: DatabaseSession, user: TokenUser, name: str, join: JoinRequest) -> dict[str, Any]:
    """Makes the user a member of a protected ladder, given its password; a public ladder needs no joining."""
    return ladder_details(join_with_password(session, user, name, join.password), user)


@router.post("/{name}/entries")
def enter_bot(session: DatabaseSession, user: TokenUser, name: str, entry: EntryRequest) -> dict[str, Any]:
    """Enters a bot of the user's in the ladder, which then plays its latest version there."""
    return ladder_details(enter_in_ladder(session, user, name, entry.bot), user)


@router.delete("/{name}/entries/{bot_name}")
def withdraw_bot(session: DatabaseSession, user: TokenUser, name: str, bot_name: str) -> dict[str, Any]:
    """Takes a bot out of the ladder, for the bot's author or the ladder's owner."""
    return ladder_details(withdraw_from_ladder(session, user, name, bot_name), user)


@router.post("/{name}/entries/{bot_name}/pause")
def pause_entry(session: DatabaseSession, user: TokenUser, name: str, bot_name: str) -> dict[str, Any]:
    return ladder_details(pause_in_ladder(session, user, name, bot_name).ladder, user)


@router.post("/{name}/entries/{bot_name}/resume")
def resume_entry(session: DatabaseSession, user: TokenUser, name: str, bot_name: str) -> dict[str, Any]:
    """Lets a paused entry play again, its count of failed starts begun anew."""
    return ladder_details(resume_in_ladder(session, user, name, bot_name).ladder, user)


def join_with_password(session: Session, user: User, name: str, password: str) -> Ladder:
    """The ladder, which the user joins if it is protected and `password` is its own; 403 if it is not."""
    ladder = _existing_ladder(session, name)
    if ladder.password_hash is not None and not is_member(ladder, user):
        if not password_matches(password, ladder.password_hash):
            raise HTTPException(403, f"That is not {ladder.name}'s password")
        ladder.members.append(LadderMember(user_id=user.id))
        session.commit()
    return ladder


def enter_in_ladder(session: Session, user: User, name: str, bot_name: str) -> Ladder:
    """Enters the user's bot in the ladder, whose member the user becomes; 403 for another's bot, 409 for a retired
    ladder or bot, a bot with no version, or one in the ladder already."""
    ladder = visible_ladder(session, name, user)
    if ladder.retired_at is not None:
        raise HTTPException(409, f"{ladder.name} is retired: it takes no new entry")
    bot = bot_named(session, bot_name)
    if bot is None or bot.owner_id != user.id:
        raise HTTPException(403, f"You have no bot named {bot_name}")
    if bot.retired_at is not None:
        raise HTTPException(409, f"{bot.name} is retired")
    if bot.latest_version is None:
        raise HTTPException(409, f"{bot.name} has no version to play")
    if any(existing.bot_id == bot.id for existing in ladder.entries):
        raise HTTPException(409, f"{bot.name} is in {ladder.name} already")
    ladder.entries.append(LadderEntry(bot_id=bot.id))
    if not is_member(ladder, user):
        ladder.members.append(LadderMember(user_id=user.id))
    session.commit()
    return ladder


def withdraw_from_ladder(session: Session, user: User, name: str, bot_name: str) -> Ladder:
    """Takes the bot out of the ladder, for its author or the ladder's owner; 403 for anyone else, and 409 in a retired
    ladder, whose standings stay as they ended."""
    ladder = _open_ladder(visible_ladder(session, name, user))
    entry = _entry_of(ladder, bot_name)
    if not may_withdraw(entry, user):
        raise HTTPException(
            403, f"{entry.bot.name} is {entry.bot.owner.login}'s to withdraw, or {ladder.owner.login}'s"
        )
    ladder.entries.remove(entry)
    session.commit()
    return ladder


def pause_in_ladder(session: Session, user: User, name: str, bot_name: str) -> LadderEntry:
    """Pauses the author's entry; 403 for anyone else."""
    entry = _authors_entry(session, name, bot_name, user)
    entry.paused = True
    entry.paused_because = f"paused by {user.login}"
    session.commit()
    return entry


def resume_in_ladder(session: Session, user: User, name: str, bot_name: str) -> LadderEntry:
    """Lets the author's paused entry play again, its count of failed starts begun anew; 403 for anyone else."""
    entry = _authors_entry(session, name, bot_name, user)
    entry.paused = False
    entry.paused_because = None
    entry.failed_starts_in_a_row = 0
    session.commit()
    return entry


def visible_ladders(session: Session, user: User | None) -> list[Ladder]:
    """The ladders `user` sees, by name, but the retired: every public one, and the protected ones they are a member
    of."""
    ladders = session.scalars(
        select(Ladder).where(Ladder.retired_at.is_(None)).options(selectinload(Ladder.members)).order_by(Ladder.name)
    )
    return [ladder for ladder in ladders if not ladder.is_protected or is_member(ladder, user)]


def may_withdraw(entry: LadderEntry, user: User | None) -> bool:
    """Whether `user` may take the entry out of its ladder: its bot's author or the ladder's owner."""
    return user is not None and user.id in (entry.bot.owner_id, entry.ladder.owner_id)


def may_enter(ladder: Ladder, user: User | None) -> bool:
    """Whether `user` may enter bots in the ladder: anyone signed in in a public one, its members in a protected one,
    and nobody in a retired one."""
    return user is not None and ladder.retired_at is None and (not ladder.is_protected or is_member(ladder, user))


@router.get("/{name}/standings")
def ladder_standings(session: DatabaseSession, user: TokenUser, name: str) -> list[dict[str, Any]]:
    """The ladder's entered bots by their latest versions' ratings, the unrated last."""
    return [standing_summary(standing) for standing in standings(session, visible_ladder(session, name, user))]


def standing_summary(standing: Standing) -> dict[str, Any]:
    rating = standing.rating
    return {
        "bot": standing.entry.bot.name,
        "author": standing.entry.bot.owner.login,
        "race": standing.entry.bot.race,
        "version": standing.version.number,
        "rating": standing.displayed_rating,
        "interval": standing.interval,
        "games": rating.games if rating else 0,
        "wins": rating.wins if rating else 0,
        "losses": rating.losses if rating else 0,
        "ties": rating.ties if rating else 0,
        "crashes": rating.crashes if rating else 0,
        "paused": standing.entry.paused,
    }


def ladder_named(session: Session, name: str) -> Ladder | None:
    """The ladder named `name`, in any case."""
    return session.scalar(select(Ladder).where(func.lower(Ladder.name) == name.lower()))


def is_member(ladder: Ladder, user: User | None) -> bool:
    return user is not None and any(member.user_id == user.id for member in ladder.members)


def visible_ladder(session: Session, name: str, user: User | None) -> Ladder:
    """The ladder named `name`; 404 if there is none, and 403 if it is protected and the user, if any, no member."""
    ladder = _existing_ladder(session, name)
    if ladder.is_protected and not is_member(ladder, user):
        raise HTTPException(403, f"{ladder.name} is protected: join it with its password first")
    return ladder


def ladder_summary(ladder: Ladder, user: User | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": ladder.name,
        "owner": ladder.owner.login,
        "protected": ladder.is_protected,
        "retired_at": ladder.retired_at.isoformat() if ladder.retired_at else None,
    }
    if not ladder.is_protected or is_member(ladder, user):
        summary |= {"maps": len(ladder.maps), "bots": len(ladder.entries), "member": is_member(ladder, user)}
    return summary


def ladder_details(ladder: Ladder, user: User) -> dict[str, Any]:
    return {
        **ladder_summary(ladder, user),
        "maps": [{"name": map_file.name, "sha256": map_file.sha256} for map_file in ladder.maps],
        "game_limits": ladder.game_limits,
        "resource_limits": ladder.resource_limits,
        "entries": [
            {
                "bot": entry.bot.name,
                "author": entry.bot.owner.login,
                "version": entry.bot.latest_version.number if entry.bot.latest_version else None,
                "paused": entry.paused,
                "paused_because": entry.paused_because,
            }
            for entry in sorted(ladder.entries, key=lambda entry: entry.bot.name.lower())
        ],
    }


def _existing_ladder(session: Session, name: str) -> Ladder:
    ladder = ladder_named(session, name)
    if ladder is None:
        raise HTTPException(404, f"No ladder is named {name}")
    return ladder


def owned_ladder(session: Session, name: str, user: User) -> Ladder:
    """The ladder named `name`; 404 if there is none, and 403 if it is not the user's."""
    ladder = _existing_ladder(session, name)
    if ladder.owner_id != user.id:
        raise HTTPException(403, f"{ladder.name} is {ladder.owner.login}'s to change")
    return ladder


def _open_ladder(ladder: Ladder) -> Ladder:
    """The ladder, unless it is retired: 409 then, since its standings stay as they ended."""
    if ladder.retired_at is not None:
        raise HTTPException(409, f"{ladder.name} is retired: its standings stay as they ended")
    return ladder


def _entry_of(ladder: Ladder, bot_name: str) -> LadderEntry:
    entry = next((entry for entry in ladder.entries if entry.bot.name.lower() == bot_name.lower()), None)
    if entry is None:
        raise HTTPException(404, f"{bot_name} is not in {ladder.name}")
    return entry


def _authors_entry(session: Session, name: str, bot_name: str, user: User) -> LadderEntry:
    entry = _entry_of(_open_ladder(visible_ladder(session, name, user)), bot_name)
    if entry.bot.owner_id != user.id:
        raise HTTPException(403, f"{entry.bot.name} is {entry.bot.owner.login}'s")
    return entry


def _add_map(request: Request, session: Session, ladder: Ladder, upload: UploadFile, *, uploader: User) -> None:
    """Adds the uploaded map to the ladder under the name of its file, which is kept once whichever ladders hold it.

    409 if the ladder holds another map of the same name.
    """
    map_name = map_name_of(upload.filename or "")
    file_store: FileStore = request.app.state.file_store
    stored, _ = file_store.store(
        upload.file,
        kind=MAP_FILES,
        suffix=MAP_SUFFIX,
        max_bytes=request.app.state.config.max_map_upload_mib * _MEBIBYTE,
        check=check_map,
    )
    map_file = session.scalar(select(MapFile).where(MapFile.name == map_name, MapFile.sha256 == stored.sha256))
    if map_file is None:
        map_file = MapFile(
            name=map_name, sha256=stored.sha256, size_bytes=stored.size_bytes, uploaded_by_id=uploader.id
        )
        session.add(map_file)
    same_name = next((held for held in ladder.maps if held.name == map_file.name), None)
    if same_name is not None and same_name.sha256 != map_file.sha256:
        raise HTTPException(409, f"{ladder.name} holds another map named {map_file.name}")
    if same_name is None:
        ladder.maps.append(map_file)


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        raise UploadRefusedError(f"limits is a JSON object, not {text!r}") from None
    if not isinstance(value, dict):
        raise UploadRefusedError(f"limits is a JSON object, not {text!r}")
    return value
