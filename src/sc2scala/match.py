from dataclasses import dataclass, field
from pathlib import Path

from sc2scala.bot import Bot
from sc2scala.game_limits import GameLimits


@dataclass(frozen=True)
class Match:
    """A match to play: two bots, in their seats, on a map, within limits.

    `map_file` is a `.SC2Map` file; any other suffix raises `ValueError`.
    """

    player_1: Bot
    player_2: Bot
    map_file: Path
    limits: GameLimits = field(default_factory=GameLimits)

    def __post_init__(self) -> None:
        if self.map_file.suffix != ".SC2Map":
            raise ValueError(f"A map is a .SC2Map file, not {self.map_file}")

    @property
    def map_name(self) -> str:
        """The map's file name without its suffix, as the engine names it."""
        return self.map_file.stem
