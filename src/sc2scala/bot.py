import re
from dataclasses import dataclass
from pathlib import Path

from sc2scala.bot_type import BotType
from sc2scala.race import Race

_BOT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class Bot:
    """A bot as the engine plays it: its name, race and type, and the folder that holds it.

    The name is made of letters, digits, `_`, `-` and `.`, and begins with a letter, a digit or `_`, since the engine
    finds the entry point by it, writes it into its match file and names folders for it; any other raises `ValueError`.
    """

    name: str
    race: Race
    bot_type: BotType
    folder: Path

    def __post_init__(self) -> None:
        if not _BOT_NAME_PATTERN.fullmatch(self.name):
            raise ValueError(
                f"A bot's name is made of letters, digits, '_', '-' and '.', and begins with a letter, a digit or '_', "
                f"not {self.name!r}"
            )

    @classmethod
    def from_folder(cls, folder: Path, *, race: Race = Race.RANDOM) -> "Bot":
        """The bot in `folder`, named for the folder, of the type its entry point shows (`BotType.from_folder`)."""
        return cls(name=folder.name, race=race, bot_type=BotType.from_folder(folder), folder=folder)
