"""How a runner machine runs: the most matches it may play at once, how much disk its cache may take, and when it plays.

Read from `runner.toml` beside the CLI's credentials, which `sc2scala runner register` writes with its defaults. How
many matches it plays at once, up to its most, and pausing it, are its owner's to set on the server.
"""

import math
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from sc2scala.client_credentials import default_credentials_file
from sc2scala.engine_choice import DEFAULT_ENGINE, ENGINE_NAMES
from sc2scala.errors import ScalaError

_CONFIG_FILE_NAME = "runner.toml"
_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MINUTES_PER_DAY = 24 * 60
_GIBIBYTE = 1024**3
_CORES_PER_MATCH = 4
_MEMORY_GIB_PER_MATCH = 8
DEFAULT_CACHE_GIB = 20
MOST_MATCHES_AT_ONCE = 100
"""The most matches a machine may allow at once, or its owner set it to play."""


class RunnerConfigError(ScalaError):
    """The runner's config file is not what it should be."""


@dataclass(frozen=True)
class PlayingWindow:
    """A weekly window in which a machine takes matches, in its local time.

    It opens on each of `days`, 0 for Monday, `start_minute` minutes after midnight, and closes `end_minute` minutes
    after midnight, on the next day if that is not after `start_minute`: a window crossing midnight belongs to the day
    it starts on.
    """

    days: frozenset[int]
    start_minute: int
    end_minute: int

    def contains(self, moment: datetime) -> bool:
        """Whether `moment`, a local time with no time zone, falls in the window."""
        minute = moment.hour * 60 + moment.minute
        day = moment.weekday()
        if self.end_minute <= self.start_minute:
            return (day in self.days and minute >= self.start_minute) or (
                (day - 1) % 7 in self.days and minute < self.end_minute
            )
        return day in self.days and self.start_minute <= minute < self.end_minute

    def next_opening(self, after: datetime) -> datetime:
        """When the window next opens after `after`, both local times with no time zone."""
        midnight = after.replace(hour=0, minute=0, second=0, microsecond=0)
        for days_ahead in range(8):
            day = midnight + timedelta(days=days_ahead)
            opening = day + timedelta(minutes=self.start_minute)
            if day.weekday() in self.days and opening > after:
                return opening
        raise AssertionError(f"{self} has no day")


@dataclass(frozen=True)
class PlayingSchedule:
    """The windows in which a machine takes matches; with none, it takes them at any hour."""

    windows: tuple[PlayingWindow, ...] = ()

    def next_opening(self, now: datetime) -> datetime | None:
        """When a window next opens, as a local time with no time zone, or `None` if one is open at `now`."""
        if not self.windows or any(window.contains(now) for window in self.windows):
            return None
        return min(window.next_opening(now) for window in self.windows)


ANY_HOUR = PlayingSchedule()
"""The schedule of a machine that plays at any hour, with no window."""


@dataclass(frozen=True)
class RunnerConfig:
    """The most matches a machine may play at once, `None` for as many as suggested for its cores and memory; how many
    GiB of bots and maps it keeps; when it plays; and with which engine, one of `ENGINE_NAMES`.

    `runners`, how many it played at once before step 9 moved the number to the server, is kept only to say that it is
    read no more.
    """

    max_matches_at_once: int | None = None
    cache_gib: float = DEFAULT_CACHE_GIB
    schedule: PlayingSchedule = ANY_HOUR
    engine: str = DEFAULT_ENGINE
    runners: int | None = None

    @property
    def cache_bytes(self) -> int:
        return int(self.cache_gib * _GIBIBYTE)

    @classmethod
    def load(cls, file: Path | None = None) -> "RunnerConfig":
        """The config in `file`, by default the user's, or the defaults if it does not exist.

        Raises `RunnerConfigError` if the file is no TOML, or holds a setting that does not exist or a value that
        does not fit it.
        """
        file = file or default_runner_config_file()
        if not file.exists():
            return cls()
        try:
            content = tomllib.loads(file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise RunnerConfigError(f"{file} is no TOML: {error}") from None
        try:
            return cls._from_toml(content)
        except RunnerConfigError as error:
            raise RunnerConfigError(f"{file}: {error}") from None

    @classmethod
    def _from_toml(cls, content: dict[str, Any]) -> "RunnerConfig":
        _refuse_unknown(content, {"max_matches_at_once", "runners", "cache_gib", "window", "engine"}, "setting")
        max_matches_at_once = content.get("max_matches_at_once")
        if max_matches_at_once is not None and not (
            isinstance(max_matches_at_once, int)
            and not isinstance(max_matches_at_once, bool)
            and 1 <= max_matches_at_once <= MOST_MATCHES_AT_ONCE
        ):
            raise RunnerConfigError(
                f"max_matches_at_once is a whole number from 1 to {MOST_MATCHES_AT_ONCE}, not {max_matches_at_once!r}"
            )
        runners = content.get("runners")
        if runners is not None and (not isinstance(runners, int) or isinstance(runners, bool) or runners < 1):
            raise RunnerConfigError(f"runners is a whole number of 1 or more, not {runners!r}")
        cache_gib = content.get("cache_gib", DEFAULT_CACHE_GIB)
        if (
            not isinstance(cache_gib, int | float)
            or isinstance(cache_gib, bool)
            or not math.isfinite(cache_gib)
            or cache_gib <= 0
        ):
            raise RunnerConfigError(f"cache_gib is a positive number, not {cache_gib!r}")
        windows = content.get("window", [])
        if not isinstance(windows, list):
            raise RunnerConfigError("each window is a table of its own, `[[window]]`")
        engine = content.get("engine", DEFAULT_ENGINE)
        if engine not in ENGINE_NAMES:
            raise RunnerConfigError(f"engine is one of {', '.join(map(repr, ENGINE_NAMES))}, not {engine!r}")
        return cls(
            max_matches_at_once=max_matches_at_once,
            cache_gib=cache_gib,
            schedule=PlayingSchedule(tuple(_window_from(window) for window in windows)),
            engine=engine,
            runners=runners,
        )


def default_runner_config_file() -> Path:
    return default_credentials_file().with_name(_CONFIG_FILE_NAME)


def suggested_matches_at_once(*, cores: int, memory_bytes: int) -> int:
    """How many matches a machine of `cores` and `memory_bytes` plays at once with room to spare: one per 4 cores and
    8 GiB, from step 1's measurements, a match of light bots taking about 2.2 GB and 1.5 cores at its peak; at most
    `MOST_MATCHES_AT_ONCE`."""
    fitting = min(cores // _CORES_PER_MATCH, memory_bytes // (_MEMORY_GIB_PER_MATCH * _GIBIBYTE))
    return max(1, min(fitting, MOST_MATCHES_AT_ONCE))


def default_config_text(*, cores: int, memory_bytes: int) -> str:
    """The config file `sc2scala runner register` writes, with the defaults, for a machine of `cores` and
    `memory_bytes`."""
    memory_gib = memory_bytes / _GIBIBYTE
    suggested = suggested_matches_at_once(cores=cores, memory_bytes=memory_bytes)
    return f"""\
# How this machine runs its runners, read when `sc2scala runner start` starts.

# The most matches it may play at once, whatever its page on the server asks: by default {suggested}, which fit
# this machine's {cores} cores and {memory_gib:.0f} GiB of memory, one per {_CORES_PER_MATCH} cores and \
{_MEMORY_GIB_PER_MATCH} GiB, for light bots.
# Set it lower if the machine does other work. How many it plays at once, 1 at first, and pausing it, are set on
# your account page on the server, or by `sc2scala runner set`. A ladder's default limits let one match take up to
# 15 GiB.
# max_matches_at_once = {suggested}

# How many GiB of bots and maps it keeps; past that, those least recently used go first.
cache_gib = {DEFAULT_CACHE_GIB}

# Which engine plays its matches: "aiarena", AI Arena's, by default, or "scala", Scala's own, which is being tried.
# engine = "scala"

# When it takes matches, in its local time; with no window, at any hour. A window that crosses midnight belongs to
# the days it starts on, and a match being played when a window closes is played to its end. `days` is a list of
# days and ranges, such as "mon-fri" or "mon,wed,sat-sun", every day if left out; `from` and `to` default to
# "00:00" and "24:00".
#
# [[window]]
# days = "mon-fri"
# from = "23:00"
# to = "07:00"
#
# [[window]]
# days = "sat-sun"
"""


def machine_cores_and_memory() -> tuple[int, int]:
    """The cores this process may run on and the machine's bytes of memory, as its processes see them: in WSL2, its
    virtual machine's."""
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    return cores, os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def _window_from(content: Any) -> PlayingWindow:
    if not isinstance(content, dict):
        raise RunnerConfigError("each window is a table of its own, `[[window]]`")
    _refuse_unknown(content, {"days", "from", "to"}, "window setting")
    start_minute = _minute_of(content.get("from", "00:00"), "from")
    end_minute = _minute_of(content.get("to", "24:00"), "to")
    if start_minute == _MINUTES_PER_DAY:
        raise RunnerConfigError("a window starts by 23:59, not at 24:00")
    if start_minute == end_minute:
        raise RunnerConfigError(
            f"a window does not end as it starts, at {start_minute // 60:02}:{start_minute % 60:02}"
        )
    return PlayingWindow(
        days=_days_of(content.get("days", "mon-sun")), start_minute=start_minute, end_minute=end_minute
    )


def _days_of(text: Any) -> frozenset[int]:
    """The days `text` names, such as "mon-fri" or "mon,wed,sat-sun"; a range may wrap past Sunday, as "fri-mon"."""
    if not isinstance(text, str):
        raise RunnerConfigError(f'days is a text such as "mon-fri", not {text!r}')
    days: set[int] = set()
    for part in text.lower().replace(" ", "").split(","):
        first, _, last = part.partition("-")
        start, end = _day_of(first), _day_of(last or first)
        days.update((start + offset) % 7 for offset in range((end - start) % 7 + 1))
    return frozenset(days)


def _day_of(name: str) -> int:
    if name not in _DAY_NAMES:
        raise RunnerConfigError(f"{name!r} is no day: one of {', '.join(_DAY_NAMES)}")
    return _DAY_NAMES.index(name)


def _minute_of(value: Any, setting: str) -> int:
    """The minute of the day `value` names, "HH:MM" from "00:00" to "24:00", or a TOML time."""
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    hours, _, minutes = str(value).partition(":")
    if not (isinstance(value, str) and hours.isdigit() and minutes.isdigit() and len(minutes) == 2):
        raise RunnerConfigError(f'{setting} is a time such as "23:00", not {value!r}')
    minute = int(hours) * 60 + int(minutes)
    if int(minutes) >= 60 or minute > _MINUTES_PER_DAY:
        raise RunnerConfigError(f'{setting} is a time from "00:00" to "24:00", not {value!r}')
    return minute


def _refuse_unknown(content: dict[str, Any], known: set[str], kind: str) -> None:
    unknown = sorted(set(content) - known)
    if unknown:
        raise RunnerConfigError(f"no {kind} is named {unknown[0]!r}: one of {', '.join(sorted(known))}")
