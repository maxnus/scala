"""Scala: StarCraft II ladders between bots, played on runners their members contribute."""

from sc2scala.__about__ import __version__
from sc2scala.aiarena_engine import AIARENA_IMAGE_TAG, AiArenaEngine
from sc2scala.bot import Bot
from sc2scala.bot_type import BotType
from sc2scala.errors import EngineError, MatchStoppedError, MatchTimeoutError, ScalaError
from sc2scala.game_limits import GameLimits
from sc2scala.match import Match
from sc2scala.match_engine import MatchEngine
from sc2scala.match_result import MatchResult
from sc2scala.race import Race
from sc2scala.resource_limits import ResourceLimits
from sc2scala.result_code import ResultCode

__all__ = [
    "AIARENA_IMAGE_TAG",
    "AiArenaEngine",
    "Bot",
    "BotType",
    "EngineError",
    "GameLimits",
    "Match",
    "MatchEngine",
    "MatchResult",
    "MatchStoppedError",
    "MatchTimeoutError",
    "Race",
    "ResourceLimits",
    "ResultCode",
    "ScalaError",
    "__version__",
]
