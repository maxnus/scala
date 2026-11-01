from dataclasses import dataclass, field
from pathlib import Path

from sc2scala.bot import Bot
from sc2scala.computer import Computer
from sc2scala.game_limits import GameLimits
from sc2scala.resource_limits import ResourceLimits


@dataclass(frozen=True)
class Match:
    """A match to play: two players, in their seats, on a map, within limits of time and of resources.

    Player 2 is a bot or the game's built-in computer; player 1 is always a bot, since the game makes a bot that plays
    the computer player 1 wherever the computer's seat is (in game, 4.10, `docs/plan.md`'s step 8b). `map_file` is a
    `.SC2Map` file; any other suffix raises `ValueError`.
    """

    player_1: Bot
    player_2: Bot | Computer
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

    @property
    def bots(self) -> tuple[Bot, ...]:
        """The match's bots, in their seats: player 1, and player 2 unless it is the computer."""
        return (self.player_1,) if isinstance(self.player_2, Computer) else (self.player_1, self.player_2)

    @property
    def computer(self) -> Computer | None:
        """Player 2 if it is the computer, or `None`."""
        return self.player_2 if isinstance(self.player_2, Computer) else None
