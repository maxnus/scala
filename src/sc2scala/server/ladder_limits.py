from dataclasses import asdict, fields
from typing import Any

from sc2scala.errors import UploadRefusedError
from sc2scala.game_limits import GameLimits
from sc2scala.resource_limits import ResourceLimits


class LimitRefusedError(UploadRefusedError):
    """A ladder's limit named that does not exist, or given a value that is no positive number."""


def limits_changed(
    changes: dict[str, Any], *, game_limits: dict[str, Any], resource_limits: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A ladder's game and resource limits, all of them, as stored, with `changes` made, each named as the field of
    `GameLimits` or `ResourceLimits` it sets.

    Raises `LimitRefusedError` for a name of neither, or a value that is not a positive number, or not a whole one
    where the field is.
    """
    game = asdict(GameLimits(**game_limits))
    resource = asdict(ResourceLimits(**resource_limits))
    types = {field.name: field.type for limits in (GameLimits, ResourceLimits) for field in fields(limits)}
    for name, value in changes.items():
        if name not in types:
            raise LimitRefusedError(f"No limit is named {name!r}: one of {', '.join(sorted(types))}")
        (game if name in game else resource)[name] = _positive_number(name, value, whole=types[name] in (int, "int"))
    return game, resource


def _positive_number(name: str, value: Any, *, whole: bool) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LimitRefusedError(f"{name} is a number, not {value!r}") from None
    if number <= 0 or (whole and not number.is_integer()):
        raise LimitRefusedError(f"{name} is a positive {'whole ' if whole else ''}number, not {value!r}")
    return int(number) if whole else number
