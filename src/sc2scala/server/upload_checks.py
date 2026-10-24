"""What the server checks of a map before it keeps it; a bot's zip is checked by `sc2scala.bot_archive`."""

import re
from pathlib import Path

from sc2scala.errors import UploadRefusedError

_MAP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_MAP_SIGNATURE = b"MPQ"
MAP_SUFFIX = ".SC2Map"


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
