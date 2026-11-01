from dataclasses import dataclass
from enum import StrEnum

from sc2scala.race import Race

COMPUTER_NAME_PREFIX = "Computer-"
"""How a computer's name begins, which no bot's may, so that no bot passes for one."""


class Difficulty(StrEnum):
    """The built-in computer's difficulty, valued as the protocol names it."""

    VERY_EASY = "VeryEasy"
    EASY = "Easy"
    MEDIUM = "Medium"
    MEDIUM_HARD = "MediumHard"
    HARD = "Hard"
    HARDER = "Harder"
    VERY_HARD = "VeryHard"
    CHEAT_VISION = "CheatVision"
    CHEAT_MONEY = "CheatMoney"
    CHEAT_INSANE = "CheatInsane"

    @classmethod
    def from_text(cls, text: str) -> "Difficulty":
        """The difficulty `text` names, in any case; raises `ValueError` for any other."""
        return _member_named(cls, text, "difficulty")


class Build(StrEnum):
    """The built-in computer's build, valued as the protocol names it; `RANDOM` is the game's own pick."""

    RANDOM = "RandomBuild"
    RUSH = "Rush"
    TIMING = "Timing"
    POWER = "Power"
    MACRO = "Macro"
    AIR = "Air"

    @classmethod
    def from_text(cls, text: str) -> "Build":
        """The build `text` names, in any case, `Random` for `RandomBuild` too; raises `ValueError` for any other."""
        if text.lower() == "random":
            return cls.RANDOM
        return _member_named(cls, text, "build")


_RACE_WORDS = {Race.TERRAN: "Terran", Race.ZERG: "Zerg", Race.PROTOSS: "Protoss", Race.RANDOM: "Random"}
"""Each race as the protocol names it."""


@dataclass(frozen=True)
class Computer:
    """The game's built-in computer as an opponent: its race, difficulty and build.

    A random race and a random build are the game's own, so that the bot playing it is not told which it got.
    """

    race: Race
    difficulty: Difficulty
    build: Build = Build.RANDOM

    @property
    def name(self) -> str:
        """The computer's name, in the protocol's words, as `Computer-VeryHard-Zerg-Rush`."""
        return f"{COMPUTER_NAME_PREFIX}{self.difficulty.value}-{_RACE_WORDS[self.race]}-{self.build.value}"

    @classmethod
    def from_name(cls, name: str) -> "Computer":
        """The computer `name` names, as `name` gives it, in any case; raises `ValueError` for any other."""
        parts = name.split("-")
        if len(parts) != 4 or not is_computer_name(name):
            raise ValueError(f"A computer is named Computer-<difficulty>-<race>-<build>, not {name!r}")
        _, difficulty, race, build = parts
        return cls(race=Race.from_text(race), difficulty=Difficulty.from_text(difficulty), build=Build.from_text(build))


def is_computer_name(name: str) -> bool:
    """Whether `name` begins as a computer's does, in any case, which no bot's may."""
    return name.lower().startswith(COMPUTER_NAME_PREFIX.lower())


def _member_named[EnumType: StrEnum](enum: type[EnumType], text: str, what: str) -> EnumType:
    for member in enum:
        if text.lower() == member.value.lower():
            return member
    raise ValueError(f"{text!r} is no {what}: one of {', '.join(member.value for member in enum)}")
