from enum import StrEnum


class Race(StrEnum):
    """A bot's race, valued as the engine's match file writes it."""

    TERRAN = "T"
    ZERG = "Z"
    PROTOSS = "P"
    RANDOM = "R"
