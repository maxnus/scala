from dataclasses import dataclass


@dataclass(frozen=True)
class GameLimits:
    """What a match may take, its times by default as AI Arena's local setup has them.

    A game that reaches `max_game_steps` ends as a tie. The engine stops a match that takes longer than
    `max_real_time_seconds`. Either engine ends a bot's game as a time-out once the bot has sent nothing for 30
    seconds. With `max_step_time_ms`, which none is by default, a bot that takes longer for a step `step_time_strikes`
    times loses by a time-out in Scala's engine; AI Arena's v0.8.0 engine passes both on and applies neither.
    """

    max_game_steps: int = 80640
    """An hour of game time."""
    max_step_time_ms: int | None = None
    step_time_strikes: int = 10
    max_real_time_seconds: int = 7200
