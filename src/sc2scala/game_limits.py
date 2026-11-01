from dataclasses import dataclass


@dataclass(frozen=True)
class GameLimits:
    """What a match may take, by default as AI Arena's local setup has it.

    A game that reaches `max_game_steps` ends as a tie. The engine stops a match that takes longer than
    `max_real_time_seconds`. A bot that takes longer than `max_step_time_ms` for a step `step_time_strikes` times is
    to lose, but AI Arena's v0.8.0 engine passes both on and applies neither: it ends a bot's game as a time-out only
    once the bot has sent nothing for 30 seconds.
    """

    max_game_steps: int = 80640
    """An hour of game time."""
    max_step_time_ms: int = 40
    step_time_strikes: int = 10
    max_real_time_seconds: int = 7200
