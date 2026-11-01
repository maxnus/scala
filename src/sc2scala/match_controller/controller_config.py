import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RACE_LETTERS = ("T", "Z", "P", "R")


@dataclass(frozen=True)
class SeatConfig:
    """A seat of the match: its bot's name and race, as one of `RACE_LETTERS`, the game client it plays through, and
    the port the controller listens on for its bot.

    With `bot_host`, the controller takes a connection on that port only from the address that name resolves to, so
    that one bot cannot take the other's seat; without, from anywhere.
    """

    name: str
    race: str
    game_url: str
    listen_port: int
    bot_host: str | None = None

    def __post_init__(self) -> None:
        if self.race not in RACE_LETTERS:
            raise ValueError(f"A race is one of {', '.join(RACE_LETTERS)}, not {self.race!r}")


@dataclass(frozen=True)
class ComputerConfig:
    """The game's built-in computer in seat 2: its name, its race, as one of `RACE_LETTERS`, and its difficulty and
    build as the protocol names them, the build `RandomBuild` for the game's own pick."""

    name: str
    race: str
    difficulty: str
    build: str

    def __post_init__(self) -> None:
        if self.race not in RACE_LETTERS:
            raise ValueError(f"A race is one of {', '.join(RACE_LETTERS)}, not {self.race!r}")


@dataclass(frozen=True)
class ControllerConfig:
    """What the match controller needs to play a match, written by the engine as JSON.

    `seats` holds a seat for each bot: two, or one, in seat 1, whose opponent is `computer`. `map_path` is the map as
    the game finds it, and the files are paths in the controller's container. A game that passes `max_game_steps` ends
    as a tie. With `max_step_time_ms`, a bot loses by a time-out once `step_time_strikes` of its steps took longer. A
    bot that sends nothing for `request_timeout_seconds` loses by a time-out, and a game client that does not answer in
    `game_timeout_seconds` ends the match as an error. Each bot must connect within `connect_timeout_seconds` of the
    controller's start, as AI Arena's engine requires of a bot it starts, and the game must be created and every bot
    must join it within `start_timeout_seconds`.
    """

    map_path: str
    seats: tuple[SeatConfig] | tuple[SeatConfig, SeatConfig]
    replay_file: str
    result_file: str
    max_game_steps: int
    max_step_time_ms: int | None = None
    step_time_strikes: int = 10
    request_timeout_seconds: float = 30.0
    game_timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 30.0
    start_timeout_seconds: float = 180.0
    log_file: str | None = None
    computer: ComputerConfig | None = None

    def __post_init__(self) -> None:
        if len(self.seats) != (1 if self.computer else 2):
            raise ValueError(f"A match has two players: {len(self.seats)} bots and {int(bool(self.computer))} computer")

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ControllerConfig":
        """The config `data` holds, as `to_json` writes it."""
        seats = [SeatConfig(**seat) for seat in data["seats"]]
        computer = ComputerConfig(**data["computer"]) if data.get("computer") else None
        if len(seats) == 1:
            return cls(**{**data, "seats": (seats[0],), "computer": computer})
        if len(seats) == 2:
            return cls(**{**data, "seats": (seats[0], seats[1]), "computer": computer})
        raise ValueError(f"A match has one or two bots, not {len(seats)}")

    @classmethod
    def from_file(cls, config_file: Path) -> "ControllerConfig":
        return cls.from_json(json.loads(config_file.read_text(encoding="utf-8")))

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "seats": [asdict(seat) for seat in self.seats]}
