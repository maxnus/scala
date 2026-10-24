import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from loguru import logger

from sc2scala.bot import Bot
from sc2scala.bot_archive import unpack_bot_archive
from sc2scala.bot_type import BotType
from sc2scala.errors import ScalaError
from sc2scala.game_limits import GameLimits
from sc2scala.match import Match
from sc2scala.match_engine import MatchEngine
from sc2scala.match_result import MatchResult
from sc2scala.race import Race
from sc2scala.resource_limits import ResourceLimits
from sc2scala.scala_client import ScalaClient

_MAX_UNPACKED_BOT_BYTES = 4 * 1024 * 1024 * 1024
_ENGINE_LOG_FOLDER_NAME = "engine"


class Runner:
    """Plays the matches a server draws for this machine, one at a time, with `engine`, and reports each.

    Bots and maps are downloaded once into `cache_folder`, by their hashes; each match is played in a folder of its own
    in `work_folder`, removed once its result is reported, and kept if it is not.
    """

    def __init__(self, client: ScalaClient, engine: MatchEngine, *, cache_folder: Path, work_folder: Path) -> None:
        self.client = client
        self.engine = engine
        self.cache_folder = cache_folder
        self.work_folder = work_folder

    def run(self, *, poll_seconds: float, once: bool = False) -> None:
        """Plays match after match, waiting `poll_seconds` whenever none was played; with `once`, asks once."""
        while True:
            played = self.play_next()
            if once:
                return
            if not played:
                time.sleep(poll_seconds)

    def play_next(self) -> bool:
        """Asks for a match, plays it and reports it; returns whether it played one to a result."""
        offered = self.client.next_match()
        if offered is None:
            logger.info("No match to play")
            return False
        match_folder = self.work_folder / f"match-{offered['id']}"
        description = f"Match {offered['id']} in {offered['ladder']}, " + " vs ".join(
            player["bot"] for player in offered["players"]
        )
        try:
            shutil.rmtree(match_folder, ignore_errors=True)
            match = self._match_from(offered, match_folder)
            logger.info(f"{description} on {offered['map']['name']}")
            result = self.engine.play(match, output_folder=match_folder / "output")
        except ScalaError as error:
            logger.error(f"{description} could not be played: {error}")
            self.client.report_failure(offered["id"], str(error))
            return False
        self.client.report_result(
            offered["id"], _result_report(result), files=_result_files(match, result, match_folder)
        )
        logger.info(f"{description}: {result.code.value}, reported")
        shutil.rmtree(match_folder, ignore_errors=True)
        return True

    def _match_from(self, offered: dict[str, Any], match_folder: Path) -> Match:
        """The match the server offered, its bots unpacked and its map named in `match_folder`."""
        bots = [self._bot_from(player, match_folder / "bots" / str(player["seat"])) for player in offered["players"]]
        map_file = match_folder / "maps" / f"{offered['map']['name']}.SC2Map"
        map_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._cached("maps", offered["map"]["sha256"], ".SC2Map"), map_file)
        return Match(
            player_1=bots[0],
            player_2=bots[1],
            map_file=map_file,
            limits=GameLimits(**offered["game_limits"]),
            resource_limits=ResourceLimits(**offered["resource_limits"]),
        )

    def _bot_from(self, player: dict[str, Any], seat_folder: Path) -> Bot:
        folder = seat_folder / player["bot"]
        archive = self._cached("bots", player["sha256"], ".zip")
        unpack_bot_archive(archive, folder, max_unpacked_bytes=_MAX_UNPACKED_BOT_BYTES)
        return Bot(name=player["bot"], race=Race(player["race"]), bot_type=BotType(player["type"]), folder=folder)

    def _cached(self, kind: str, sha256: str, suffix: str) -> Path:
        """The file of `kind` with that hash, downloaded into the cache if it is not there yet."""
        path = self.cache_folder / kind / f"{sha256}{suffix}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self.client.download(kind, sha256, path)
        return path


def _result_report(result: MatchResult) -> dict[str, Any]:
    return {
        "code": result.code.value,
        "game_steps": result.game_steps,
        "average_step_times": list(result.average_step_times),
    }


def _result_files(match: Match, result: MatchResult, match_folder: Path) -> dict[str, Path]:
    """The replay, and the logs zipped: each bot's, from its folder, and the engine's, all the rest."""
    zips = match_folder / "zips"
    zips.mkdir(parents=True, exist_ok=True)
    files = {"replay": result.replay_file} if result.replay_file else {}
    bot_log_folders = []
    for seat, bot in enumerate((match.player_1, match.player_2), start=1):
        bot_logs = result.logs_folder / f"player_{seat}-{bot.name}"
        bot_log_folders.append(bot_logs)
        files[f"player_{seat}_logs"] = _zipped(bot_logs, zips / f"player_{seat}.zip", skipping=[])
    files["engine_logs"] = _zipped(
        result.logs_folder, zips / f"{_ENGINE_LOG_FOLDER_NAME}.zip", skipping=bot_log_folders
    )
    return files


def _zipped(folder: Path, archive: Path, *, skipping: list[Path]) -> Path:
    """`folder` zipped into `archive`, but for the folders `skipping`; an empty zip if `folder` is missing."""
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file() and not any(path.is_relative_to(skipped) for skipped in skipping):
                    zipped.write(path, path.relative_to(folder).as_posix())
    return archive
