"""The API's bots: uploading a version, listing, reading and downloading them."""

from typing import Annotated, Any, BinaryIO

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sc2scala.bot import is_valid_bot_name
from sc2scala.bot_archive import type_of_bot_archive
from sc2scala.race import Race
from sc2scala.server.file_store import FileStore
from sc2scala.server.models import BotRecord, BotVersion, User
from sc2scala.server.web import DatabaseSession, token_user

router = APIRouter(prefix="/api/bots")

BOT_ARCHIVES = "bots"
BOT_ARCHIVE_SUFFIX = ".zip"
_MAX_BOT_NAME_LENGTH = 50
_MEBIBYTE = 1024 * 1024

TokenUser = Annotated[User, Depends(token_user)]


@router.post("/{name}/versions")
def upload_version(
    request: Request,
    session: DatabaseSession,
    user: TokenUser,
    name: str,
    archive: UploadFile,
    race: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Uploads a version of the bot `name`, which is made, of `race`, at its first upload, and may change race at any.

    A zip the bot holds already is no new version.
    """
    bot, version, is_new = upload_bot_version(request, session, user, name, archive.file, race=race)
    return {"bot": bot_summary(bot), "version": version_summary(version), "new": is_new}


def upload_bot_version(
    request: Request, session: Session, user: User, name: str, archive: BinaryIO, *, race: str | None
) -> tuple[BotRecord, BotVersion, bool]:
    """Stores `archive` as the next version of the user's bot `name`, made of `race` if it is new, and returns the bot,
    the version, and whether the version is new, rather than one the bot holds already.

    Raises `HTTPException` if the name is not a bot's, the bot is someone else's, a new bot has no race, or the race is
    none; `UploadRefusedError` if the zip is not a bot's.
    """
    bot = bot_named(session, name)
    if bot is None:
        if not is_valid_bot_name(name) or len(name) > _MAX_BOT_NAME_LENGTH:
            raise HTTPException(422, f"A bot's name is letters, digits, '_', '-' and '.', 50 at most, not {name!r}")
        if race is None:
            raise HTTPException(422, "A new bot needs a race: T, Z, P or R")
    elif bot.owner_id != user.id:
        raise HTTPException(403, f"{bot.name} is {bot.owner.login}'s")
    bot_race = _race_from(race) if race is not None else None
    config = request.app.state.config
    file_store: FileStore = request.app.state.file_store
    stored, bot_type = file_store.store(
        archive,
        kind=BOT_ARCHIVES,
        suffix=BOT_ARCHIVE_SUFFIX,
        max_bytes=config.max_bot_upload_mib * _MEBIBYTE,
        check=lambda path: type_of_bot_archive(
            path, bot_name=bot.name if bot else name, max_unpacked_bytes=config.max_bot_unpacked_mib * _MEBIBYTE
        ),
    )
    if bot is None:
        bot = BotRecord(name=name, race=(bot_race or Race.RANDOM).value, owner_id=user.id)
        session.add(bot)
    elif bot_race is not None:
        bot.race = bot_race.value
    version = next((version for version in bot.versions if version.sha256 == stored.sha256), None)
    is_new = version is None
    if version is None:
        version = BotVersion(
            number=len(bot.versions) + 1, sha256=stored.sha256, size_bytes=stored.size_bytes, bot_type=bot_type.value
        )
        bot.versions.append(version)
    commit_or_conflict(session, f"Another upload of {name} came at the same moment: upload again")
    return bot, version, is_new


@router.get("")
def my_bots(session: DatabaseSession, user: TokenUser) -> list[dict[str, Any]]:
    bots = session.scalars(select(BotRecord).where(BotRecord.owner_id == user.id).order_by(BotRecord.name))
    return [bot_summary(bot) for bot in bots]


@router.get("/{name}")
def bot_details(session: DatabaseSession, user: TokenUser, name: str) -> dict[str, Any]:
    bot = _existing_bot(session, name)
    return {**bot_summary(bot), "versions": [version_summary(version) for version in bot.versions]}


@router.get("/{name}/versions/{number}/archive")
def download_version(
    request: Request, session: DatabaseSession, user: TokenUser, name: str, number: int
) -> FileResponse:
    """The zip of a version, for its author alone."""
    bot = _existing_bot(session, name)
    if bot.owner_id != user.id:
        raise HTTPException(403, f"{bot.name}'s versions are for {bot.owner.login} alone")
    version = next((version for version in bot.versions if version.number == number), None)
    if version is None:
        raise HTTPException(404, f"{bot.name} has no version {number}")
    file_store: FileStore = request.app.state.file_store
    path = file_store.path_of(BOT_ARCHIVES, version.sha256, BOT_ARCHIVE_SUFFIX)
    return FileResponse(path, media_type="application/zip", filename=f"{bot.name}-{version.number}.zip")


def commit_or_conflict(session: Session, conflict: str) -> None:
    """Commits the session, and answers 409 with `conflict` if another request broke a unique constraint first."""
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, conflict) from error


def bot_named(session: Session, name: str) -> BotRecord | None:
    """The bot named `name`, in any case."""
    return session.scalar(select(BotRecord).where(func.lower(BotRecord.name) == name.lower()))


def bot_summary(bot: BotRecord) -> dict[str, Any]:
    latest = bot.latest_version
    return {
        "name": bot.name,
        "author": bot.owner.login,
        "race": bot.race,
        "latest_version": latest.number if latest else None,
    }


def version_summary(version: BotVersion) -> dict[str, Any]:
    return {
        "number": version.number,
        "sha256": version.sha256,
        "size_bytes": version.size_bytes,
        "type": version.bot_type,
        "uploaded_at": version.uploaded_at.isoformat(),
    }


def _existing_bot(session: Session, name: str) -> BotRecord:
    bot = bot_named(session, name)
    if bot is None:
        raise HTTPException(404, f"No bot is named {name}")
    return bot


def _race_from(text: str) -> Race:
    try:
        return Race.from_text(text)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
