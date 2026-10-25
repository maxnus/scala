"""A bot's zip: what the server checks of it before keeping it, and how a runner unpacks it."""

import shutil
import stat
import zipfile
from collections.abc import Iterable
from pathlib import Path

from sc2scala.bot_type import BotType
from sc2scala.errors import UploadRefusedError

_UNPACK_CHUNK_BYTES = 1024 * 1024
_MACOS_FOLDER = "__MACOSX/"
"""The folder of file attributes macOS adds to the zips it makes, which no bot needs."""


def type_of_bot_archive(archive_path: Path, *, bot_name: str, max_unpacked_bytes: int) -> BotType:
    """The type of the bot `bot_name` in the zip at `archive_path`, by the entry point at the root of its bot folder,
    which a name may reach by `./`.

    The bot's folder is the zip's root, or the one folder all its files sit in, as a zip of the folder itself holds
    them; macOS's `__MACOSX` folder is passed over.

    Raises `UploadRefusedError` for a file that is not a zip or is damaged, a path that would unpack outside the bot's
    folder, a symbolic link, more than `max_unpacked_bytes` unpacked, counted by unpacking, no entry point, or a
    Windows bot, which the engine cannot run.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [member for member in archive.infolist() if not _is_macos_attributes(member.filename)]
            folder = _bot_folder_of(member.filename for member in members)
            root_files = set()
            for member in members:
                _check_member_path(member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise UploadRefusedError(f"The zip holds a symbolic link, {member.filename}")
                path_in_folder = _path_in_folder(member.filename, folder)
                if not member.is_dir() and "/" not in path_in_folder:
                    root_files.add(path_in_folder)
            _check_unpacked_bytes(archive, members, max_unpacked_bytes=max_unpacked_bytes)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, OSError) as error:
        raise UploadRefusedError(f"The upload is not a zip that can be unpacked: {error}") from error
    try:
        bot_type = BotType.from_file_names(root_files, bot_name=bot_name)
    except ValueError as error:
        raise UploadRefusedError(f"{error}, at the zip's root") from error
    if bot_type == BotType.CPP_WIN32:
        raise UploadRefusedError("Windows bots do not play yet: the engine's images hold no Wine")
    return bot_type


def _check_unpacked_bytes(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], *, max_unpacked_bytes: int) -> None:
    """Unpacks every file, keeping nothing, and raises `UploadRefusedError` once more than `max_unpacked_bytes` came
    out, whatever sizes the zip declares."""
    unpacked_bytes = 0
    for member in members:
        if member.is_dir():
            continue
        with archive.open(member) as unpacked:
            while chunk := unpacked.read(_UNPACK_CHUNK_BYTES):
                unpacked_bytes += len(chunk)
                if unpacked_bytes > max_unpacked_bytes:
                    raise UploadRefusedError(f"The zip unpacks to more than {max_unpacked_bytes // (1024 * 1024)} MiB")


def unpack_bot_archive(archive_path: Path, destination: Path, *, max_unpacked_bytes: int) -> None:
    """Unpacks the bot's folder in the zip at `archive_path`, as `type_of_bot_archive` finds it, into the folder
    `destination`, made if missing, the paths `./` begins taken from the folder.

    Raises `UploadRefusedError`, having written at most `max_unpacked_bytes`, for anything `type_of_bot_archive`
    refuses but the lack of an entry point.
    """
    destination.mkdir(parents=True, exist_ok=True)
    written_bytes = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [member for member in archive.infolist() if not _is_macos_attributes(member.filename)]
            folder = _bot_folder_of(member.filename for member in members)
            for member in members:
                _check_member_path(member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise UploadRefusedError(f"The zip holds a symbolic link, {member.filename}")
                path_in_folder = _path_in_folder(member.filename, folder)
                if not path_in_folder:
                    continue
                target = destination / path_in_folder
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as unpacked, target.open("wb") as written:
                    while chunk := unpacked.read(_UNPACK_CHUNK_BYTES):
                        written_bytes += len(chunk)
                        if written_bytes > max_unpacked_bytes:
                            raise UploadRefusedError(
                                f"The zip unpacks to more than {max_unpacked_bytes // (1024 * 1024)} MiB"
                            )
                        written.write(chunk)
                if member.external_attr >> 16 & 0o111:
                    target.chmod(target.stat().st_mode | 0o111)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError) as error:
        shutil.rmtree(destination, ignore_errors=True)
        raise UploadRefusedError(f"The zip cannot be unpacked: {error}") from error
    except UploadRefusedError:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _is_macos_attributes(member_path: str) -> bool:
    return member_path.removeprefix("./").startswith(_MACOS_FOLDER)


def _bot_folder_of(member_paths: Iterable[str]) -> str:
    """The folder, as `MyBot/`, that every member of the zip lies in, or `""` if some lies at its root."""
    paths = [path.removeprefix("./") for path in member_paths]
    tops = {path.split("/", 1)[0] for path in paths if path}
    if len(tops) == 1 and all("/" in path for path in paths if path):
        return f"{next(iter(tops))}/"
    return ""


def _path_in_folder(member_path: str, folder: str) -> str:
    return member_path.removeprefix("./").removeprefix(folder)


def _check_member_path(member_path: str) -> None:
    parts = member_path.split("/")
    if member_path.startswith("/") or "\\" in member_path or ".." in parts or ":" in parts[0]:
        raise UploadRefusedError(f"The zip holds a path that would unpack outside the bot's folder, {member_path}")
