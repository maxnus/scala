"""What the server checks of a bot's zip and of a map before it keeps either."""

import re
import stat
import zipfile
from pathlib import Path

from sc2scala.bot_type import BotType
from sc2scala.errors import UploadRefusedError

_MAP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_MAP_SIGNATURE = b"MPQ"
MAP_SUFFIX = ".SC2Map"
_UNPACK_CHUNK_BYTES = 1024 * 1024


def type_of_bot_archive(archive_path: Path, *, bot_name: str, max_unpacked_bytes: int) -> BotType:
    """The type of the bot `bot_name` in the zip at `archive_path`, by the entry point at its root, which a name may
    reach by `./`.

    Raises `UploadRefusedError` for a file that is not a zip or is damaged, a path that would unpack outside the bot's
    folder, a symbolic link, more than `max_unpacked_bytes` unpacked, counted by unpacking, no entry point, or a
    Windows bot, which the engine cannot run.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            root_files = set()
            for member in members:
                _check_member_path(member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise UploadRefusedError(f"The zip holds a symbolic link, {member.filename}")
                path_in_folder = member.filename.removeprefix("./")
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


def map_name_of(file_name: str) -> str:
    """The name of the map in the file `file_name`, a `.SC2Map` file; raises `UploadRefusedError` for any other."""
    path = Path(file_name)
    if path.suffix != MAP_SUFFIX or not _MAP_NAME_PATTERN.fullmatch(path.stem) or path.name != file_name:
        raise UploadRefusedError(
            f"A map is a .SC2Map file named with letters, digits, '_', '-' and '.', not {file_name!r}"
        )
    return path.stem


def check_map(map_path: Path) -> None:
    """Raises `UploadRefusedError` unless the file is an MPQ archive, as every StarCraft II map is."""
    with map_path.open("rb") as map_file:
        if map_file.read(len(_MAP_SIGNATURE)) != _MAP_SIGNATURE:
            raise UploadRefusedError("The upload is not a StarCraft II map")


def _check_member_path(member_path: str) -> None:
    parts = member_path.split("/")
    if member_path.startswith("/") or "\\" in member_path or ".." in parts or ":" in parts[0]:
        raise UploadRefusedError(f"The zip holds a path that would unpack outside the bot's folder, {member_path}")
