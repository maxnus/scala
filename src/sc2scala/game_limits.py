from dataclasses import dataclass


@dataclass(frozen=True)
class GameLimits:
    """What a match may take, by default as AI Arena's local setup has it.

    A game that reaches `max_game_steps` ends as a tie. A bot that takes longer than `max_step_time_ms` for a step
    `step_time_strikes` times loses. The engine stops a match that takes longer than `max_real_time_seconds`.
    """

    max_game_steps: int = 80640
    """An hour of game time."""
    max_step_time_ms: int = 40
    step_time_strikes: int = 10
    max_real_time_seconds: int = 7200
