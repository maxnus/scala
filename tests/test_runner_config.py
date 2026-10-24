from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sc2scala.runner_config import (
    PlayingSchedule,
    RunnerConfig,
    RunnerConfigError,
    default_config_text,
    suggested_runners,
)

_GIBIBYTE = 1024**3

_NIGHTS_AND_WEEKENDS = """\
runners = 4
cache_gib = 50.5

[[window]]
days = "mon-fri"
from = "23:00"
to = "07:00"

[[window]]
days = "sat-sun"
"""


def config_of(tmp_path: Path, text: str) -> RunnerConfig:
    file = tmp_path / "runner.toml"
    file.write_text(text, encoding="utf-8")
    return RunnerConfig.load(file)


def at(text: str) -> datetime:
    """A moment of the week of 2026-10-26, a Monday, such as "Tue 06:59"."""
    day, clock = text.split()
    hour, minute = clock.split(":")
    days_ahead = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(day)
    return datetime(2026, 10, 26, int(hour), int(minute)) + timedelta(days=days_ahead)


def test_a_machine_with_no_config_plays_one_match_at_a_time_at_any_hour(tmp_path: Path) -> None:
    config = RunnerConfig.load(tmp_path / "missing.toml")
    assert (config.runners, config.cache_gib, config.schedule) == (1, 20, PlayingSchedule())
    assert config.schedule.next_opening(at("Wed 12:00")) is None


def test_a_config_sets_the_runners_the_cache_and_the_windows(tmp_path: Path) -> None:
    config = config_of(tmp_path, _NIGHTS_AND_WEEKENDS)
    assert (config.runners, config.cache_bytes) == (4, int(50.5 * _GIBIBYTE))
    assert len(config.schedule.windows) == 2


@pytest.mark.parametrize(
    ("moment", "is_open"),
    [
        ("Mon 22:59", False),
        ("Mon 23:00", True),
        ("Tue 06:59", True),
        ("Tue 07:00", False),
        ("Sat 06:59", True),
        ("Sat 07:00", True),
        ("Sun 23:59", True),
        ("Mon 06:59", False),
    ],
)
def test_a_window_crossing_midnight_belongs_to_the_day_it_starts(tmp_path: Path, moment: str, is_open: bool) -> None:
    schedule = config_of(tmp_path, _NIGHTS_AND_WEEKENDS).schedule
    assert (schedule.next_opening(at(moment)) is None) == is_open


@pytest.mark.parametrize(
    ("moment", "opening"),
    [("Mon 12:00", "Mon 23:00"), ("Tue 07:00", "Tue 23:00"), ("Mon 06:59", "Mon 23:00")],
)
def test_a_closed_schedule_says_when_its_next_window_opens(tmp_path: Path, moment: str, opening: str) -> None:
    assert config_of(tmp_path, _NIGHTS_AND_WEEKENDS).schedule.next_opening(at(moment)) == at(opening)


def test_the_next_opening_may_be_days_ahead_and_wrap_past_sunday(tmp_path: Path) -> None:
    schedule = config_of(tmp_path, '[[window]]\ndays = "fri-mon"\nfrom = "18:00"\n').schedule
    assert schedule.next_opening(at("Tue 12:00")) == at("Fri 18:00")
    assert schedule.next_opening(at("Mon 12:00")) == at("Mon 18:00")
    assert schedule.next_opening(at("Sun 19:00")) is None


def test_days_are_listed_and_ranged_and_a_toml_time_is_a_time(tmp_path: Path) -> None:
    [window] = config_of(
        tmp_path, '[[window]]\ndays = "Mon, wed,sat-sun"\nfrom = 08:30:00\nto = "24:00"'
    ).schedule.windows
    assert (window.days, window.start_minute, window.end_minute) == (frozenset({0, 2, 5, 6}), 510, 1440)


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("runners = 0", "runners is a whole number"),
        ('runners = "4"', "runners is a whole number"),
        ("runners = true", "runners is a whole number"),
        ("cache_gib = 0", "cache_gib is a positive number"),
        ("cache_gib = inf", "cache_gib is a positive number"),
        ("cache_gib = nan", "cache_gib is a positive number"),
        ("ladders = []", "no setting is named 'ladders'"),
        ('[window]\ndays = "mon"', "each window is a table of its own"),
        ('[[window]]\ndays = "monday"', "'monday' is no day"),
        ("[[window]]\ndays = 5", "days is a text"),
        ('[[window]]\nfrom = "25:00"', 'from is a time from "00:00" to "24:00"'),
        ('[[window]]\nto = "07:60"', 'to is a time from "00:00" to "24:00"'),
        ('[[window]]\nfrom = "7pm"', 'from is a time such as "23:00"'),
        ('[[window]]\nfrom = "24:00"', "a window starts by 23:59"),
        ('[[window]]\nfrom = "07:00"\nto = "07:00"', "a window does not end as it starts, at 07:00"),
        ('[[window]]\nuntil = "07:00"', "no window setting is named 'until'"),
        ("runners = ", "is no TOML"),
    ],
)
def test_a_config_that_does_not_fit_is_refused_naming_its_file(tmp_path: Path, text: str, complaint: str) -> None:
    with pytest.raises(RunnerConfigError, match="runner.toml") as refused:
        config_of(tmp_path, text)
    assert complaint in str(refused.value)


def test_the_config_register_writes_holds_the_defaults_and_an_example_that_reads(tmp_path: Path) -> None:
    text = default_config_text(cores=32, memory_bytes=59 * _GIBIBYTE)
    assert "about 7 fit this machine's 32 cores and 59 GiB of\n# memory" in text
    assert config_of(tmp_path, text) == RunnerConfig()
    uncommented = "\n".join(
        line.removeprefix("# ") if "=" in line or "[[" in line else line for line in text.splitlines()
    )
    assert config_of(tmp_path, uncommented) == config_of(tmp_path, "\n".join(_NIGHTS_AND_WEEKENDS.splitlines()[3:]))


@pytest.mark.parametrize(("cores", "memory_gib", "runners"), [(32, 59, 7), (32, 128, 8), (8, 16, 2), (2, 4, 1)])
def test_one_runner_is_suggested_per_4_cores_and_8_gib(cores: int, memory_gib: int, runners: int) -> None:
    assert suggested_runners(cores=cores, memory_bytes=memory_gib * _GIBIBYTE) == runners
