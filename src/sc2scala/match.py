from dataclasses import dataclass, field
from pathlib import Path

from sc2scala.bot import Bot
from sc2scala.game_limits import GameLimits
from sc2scala.resource_limits import ResourceLimits


@dataclass(frozen=True)
class Match:
    """A match to play: two bots, in their seats, on a map, within limits of time and of resources.

    `map_file` is a `.SC2Map` file; any other suffix raises `ValueError`.
    """

    player_1: Bot
    player_2: Bot
    map_file: Path
    limits: GameLimits = field(default_factory=GameLimits)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)

    def __post_init__(self) -> None:
        if self.map_file.suffix != ".SC2Map":
            raise ValueError(f"A map is a .SC2Map file, not {self.map_file}")

    @property
    def map_name(self) -> str:
        """The map's file name without its suffix, as the engine names it."""
        return self.map_file.stem
