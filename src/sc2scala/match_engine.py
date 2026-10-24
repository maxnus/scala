from pathlib import Path
from typing import Protocol

from sc2scala.match import Match
from sc2scala.match_result import MatchResult


class MatchEngine(Protocol):
    """What plays a match."""

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        """Plays `match` and returns how it ended, with its replay and logs in `output_folder`.

        `output_folder` is created if it does not exist, and must be empty if it does. A match that ends badly is a
        result, `ResultCode.ERROR` among them; an engine that cannot play raises `EngineError`.
        """
        ...
