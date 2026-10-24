from enum import StrEnum


class Race(StrEnum):
    """A bot's race, valued as the engine's match file writes it."""

    TERRAN = "T"
    ZERG = "Z"
    PROTOSS = "P"
    RANDOM = "R"

    @classmethod
    def from_text(cls, text: str) -> "Race":
        """The race `text` names by its letter or its name, in any case; raises `ValueError` for any other."""
        for race in cls:
            if text.upper() in (race.value, race.name):
                return race
        raise ValueError(f"{text!r} is no race: T, Z, P or R, or its name")
