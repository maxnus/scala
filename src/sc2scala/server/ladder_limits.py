from dataclasses import asdict, dataclass, fields
from typing import Any

from sc2scala.errors import UploadRefusedError
from sc2scala.game_limits import GameLimits
from sc2scala.resource_limits import ResourceLimits

_NO_LIMIT_TEXTS = ("", "off", "none")
"""What turns off a limit that may be off."""


class LimitRefusedError(UploadRefusedError):
    """A ladder's limit named that does not exist, or given a value that is no positive number."""


def limits_changed(
    changes: dict[str, Any], *, game_limits: dict[str, Any], resource_limits: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A ladder's game and resource limits, all of them, as stored, with `changes` made, each named as the field of
    `GameLimits` or `ResourceLimits` it sets.

    A limit that may be off, whose default is none, is turned off by `None`, an empty text, `off` or `none`. Raises
    `LimitRefusedError` for a name of neither, or a value that is not a positive number, or not a whole one where the
    field is.
    """
    game = asdict(GameLimits(**game_limits))
    resource = asdict(ResourceLimits(**resource_limits))
    limit_fields = {field.name: field for limits in (GameLimits, ResourceLimits) for field in fields(limits)}
    for name, value in changes.items():
        if name not in limit_fields:
            raise LimitRefusedError(f"No limit is named {name!r}: one of {', '.join(sorted(limit_fields))}")
        limits = game if name in game else resource
        if limit_fields[name].default is None and (value is None or str(value).strip().lower() in _NO_LIMIT_TEXTS):
            limits[name] = None
        else:
            limits[name] = _positive_number(name, value, whole=limit_fields[name].type not in (float, "float"))
    return game, resource


def _positive_number(name: str, value: Any, *, whole: bool) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LimitRefusedError(f"{name} is a number, not {value!r}") from None
    if number <= 0 or (whole and not number.is_integer()):
        raise LimitRefusedError(f"{name} is a positive {'whole ' if whole else ''}number, not {value!r}")
    return int(number) if whole else number


@dataclass(frozen=True)
class LimitField:
    """A limit as a ladder's pages ask for it: its name, as `GameLimits` or `ResourceLimits` holds it, its label,
    whether it takes fractions, and whether it may be left empty, for no limit."""

    name: str
    label: str
    fractional: bool = False
    optional: bool = False


LIMIT_GROUPS: tuple[tuple[str, tuple[LimitField, ...]], ...] = (
    (
        "The game",
        (
            LimitField("max_game_steps", "Game steps before a tie, 22.4 a second"),
            LimitField("max_step_time_ms", "Milliseconds a bot may take for a step, or empty for none", optional=True),
            LimitField("step_time_strikes", "Slower steps before the bot loses, where a step has a limit"),
            LimitField("max_real_time_seconds", "Seconds of real time a match may take"),
        ),
    ),
    (
        "What a match may use",
        (
            LimitField("bot_cpus", "Cores for each bot", fractional=True),
            LimitField("bot_memory_mib", "MiB of memory for each bot"),
            LimitField("bot_processes", "Processes and threads for each bot"),
            LimitField("bot_log_mib", "MiB of logs for each bot"),
            LimitField("game_cpus", "Cores for the game", fractional=True),
            LimitField("game_memory_mib", "MiB of memory for the game"),
            LimitField("match_controller_cpus", "Cores for the match controller", fractional=True),
            LimitField("match_controller_memory_mib", "MiB of memory for the match controller"),
        ),
    ),
)
"""Every limit of a ladder, in the groups its pages show."""


def limit_values(*, game_limits: dict[str, Any], resource_limits: dict[str, Any]) -> dict[str, int | float | None]:
    """Every limit's value, as stored, the defaults for those not; `None` for one that is off."""
    return {**asdict(GameLimits(**game_limits)), **asdict(ResourceLimits(**resource_limits))}
