from dataclasses import dataclass
from pathlib import Path

from sc2scala.result_code import ResultCode

_STEPS_PER_GAME_SECOND = 22.4


@dataclass(frozen=True)
class MatchResult:
    """How a match ended, and where its replay and logs are.

    `replay_file` is `None` if the engine saved no replay. `average_step_times` holds each player's average time for
    a step, in seconds, as the engine measured it, or `None` where it reported none.
    """

    code: ResultCode
    game_steps: int
    average_step_times: tuple[float | None, float | None]
    real_time_seconds: float
    replay_file: Path | None
    logs_folder: Path

    @property
    def game_seconds(self) -> float:
        """The game time the match lasted, at the game's faster speed."""
        return self.game_steps / _STEPS_PER_GAME_SECOND
