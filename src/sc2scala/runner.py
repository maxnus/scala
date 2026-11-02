import os
import queue
import shutil
import threading
import zipfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from sc2scala.__about__ import __version__
from sc2scala.bot import Bot
from sc2scala.bot_archive import unpack_bot_archive
from sc2scala.bot_type import BotType
from sc2scala.computer import Build, Computer, Difficulty
from sc2scala.errors import ScalaError, ServerError
from sc2scala.game_limits import GameLimits
from sc2scala.match import Match
from sc2scala.match_engine import MatchEngine
from sc2scala.match_result import MatchResult
from sc2scala.race import Race
from sc2scala.resource_limits import ResourceLimits
from sc2scala.runner_config import ANY_HOUR, PlayingSchedule
from sc2scala.scala_client import ScalaClient

_MAX_UNPACKED_BOT_BYTES = 4 * 1024 * 1024 * 1024
_ENGINE_LOG_FOLDER_NAME = "engine"
_MATCH_FOLDER_PREFIX = "match-"
_CACHED_SUFFIXES = {"bots": ".zip", "maps": ".SC2Map"}
_JOIN_SECONDS = 0.2
_START_AND_REPORT_SECONDS = 300
"""The time a match is allowed beyond its real-time limit to start and report, to end by its deadline."""
DEFAULT_KEPT_MATCH_FOLDERS = 10


class Runner:
    """Plays the matches a server draws for this machine with `engine`, as many at once as its owner sets on the server,
    up to `max_matches_at_once`, and reports each.

    Bots and maps are downloaded once into `cache_folder`, by their hashes, and shared by the matches played at once.
    After each match, while the cache holds more than `cache_bytes`, the files least recently used go, but for those
    a match is using. Each match is played in a folder of its own in `work_folder`, removed once its result is
    reported, and kept if it is not, without its bots and map, the `kept_match_folders` latest of them.
    """

    def __init__(
        self,
        client: ScalaClient,
        engine: MatchEngine,
        *,
        cache_folder: Path,
        work_folder: Path,
        max_matches_at_once: int = 1,
        cache_bytes: int | None = None,
        kept_match_folders: int = DEFAULT_KEPT_MATCH_FOLDERS,
    ) -> None:
        self.client = client
        self.engine = engine
        self.cache_folder = cache_folder
        self.work_folder = work_folder
        self.max_matches_at_once = max_matches_at_once
        self.cache_bytes = cache_bytes
        self.kept_match_folders = kept_match_folders
        self._stopping = threading.Event()
        self._stopping_now = threading.Event()
        self._ask_again = threading.Event()
        self._lock = threading.Lock()
        self._cached_files_in_use: Counter[Path] = Counter()
        self._downloads: dict[Path, threading.Lock] = {}
        self._match_folders_in_play: set[Path] = set()
        self._playing: set[int] = set()
        self._settings: tuple[int, bool] | None = None
        """The matches it may play at once and whether it is paused, as the server last answered."""
        self._last_note: str | None = None

    @property
    def stopping(self) -> bool:
        """Whether the runner was asked to stop."""
        return self._stopping.is_set()

    def stop(self) -> None:
        """Takes no new match, and lets `run` return once the matches being played end. Safe in a signal handler."""
        self._stopping.set()
        self._ask_again.set()

    def stop_now(self) -> None:
        """Stops the matches being played as well, which are reported unplayed, so void. Safe in a signal handler."""
        self._stopping_now.set()
        self.stop()

    def run(self, *, poll_seconds: float, schedule: PlayingSchedule = ANY_HOUR) -> None:
        """Plays matches until `stop` is called, as many at once as the server says, up to `max_matches_at_once`.

        One thread asks for them, one ask at a time, so that each ask names every match being played; it asks again at
        once while the server's answer leaves a match free, when a match ends, and else after `poll_seconds`, outside
        the windows of `schedule` too, for none, so that the server sees the machine and the runner follows a change of
        its settings. Each of `max_matches_at_once` workers plays the matches handed to it.

        First removes what matches cut short left, as `remove_leftovers` does. Raises `ScalaError` if a thread failed in
        a way it did not foresee, once the others have stopped, which it asks them to.
        """
        self.remove_leftovers()
        failures: list[Exception] = []
        offered: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()
        workers = [
            threading.Thread(target=self._work, args=(offered, failures), name=f"runner-{number}")
            for number in range(1, self.max_matches_at_once + 1)
        ]
        asking = threading.Thread(
            target=self._ask_for_matches,
            args=(offered, poll_seconds, schedule, len(workers), failures),
            name="runner-asking",
        )
        threads = [asking, *workers]
        for thread in threads:
            thread.start()
        noted_stopping = noted_stopping_now = False
        while alive := [thread for thread in threads if thread.is_alive()]:
            if self._stopping.is_set() and not noted_stopping:
                noted_stopping = True
                logger.info("Taking no new match, and stopping once the matches being played end")
            if self._stopping_now.is_set() and not noted_stopping_now:
                noted_stopping_now = True
                logger.info("Stopping the matches being played")
                self.engine.stop_matches()
            alive[0].join(timeout=_JOIN_SECONDS)
        if failures:
            raise ScalaError(f"The runner stopped after a failure of its own: {failures[0]!r}") from failures[0]

    def play_next(self) -> bool:
        """Asks for a match, plays it and reports it; returns whether the server took a result.

        Raises `ServerError` if the server cannot be asked, or cannot be told that a match could not be played.
        """
        offered = self._ask(next_window_opens=None)
        if offered is None:
            return False
        try:
            return self._play_in_its_folder(offered)
        finally:
            self._played(offered)

    def _ask_for_matches(
        self,
        offered: queue.SimpleQueue[dict[str, Any] | None],
        poll_seconds: float,
        schedule: PlayingSchedule,
        workers: int,
        failures: list[Exception],
    ) -> None:
        """Asks for matches until the runner stops, and hands each to the workers, then tells each worker to stop."""
        try:
            while not self._stopping.is_set():
                self._ask_again.clear()
                opening = schedule.next_opening(datetime.now())
                if opening is not None:
                    self._note(f"No playing window is open; the next opens {opening:%a %H:%M}")
                try:
                    match = self._ask(next_window_opens=opening)
                except ServerError as error:
                    logger.error(f"Asking the server failed, and is tried again in {poll_seconds:.0f} s: {error}")
                    match = None
                if match is not None:
                    offered.put(match)
                    if self._may_play_another():
                        continue
                wait = poll_seconds if opening is None else min(poll_seconds, _seconds_until(opening))
                self._ask_again.wait(wait)
        except Exception as error:
            logger.exception("Asking for matches failed")
            failures.append(error)
            self.stop()
        finally:
            for _ in range(workers):
                offered.put(None)

    def _ask(self, *, next_window_opens: datetime | None) -> dict[str, Any] | None:
        """Asks the server for a match, naming those being played, and follows its answer; returns the match it
        leased, counted among those played from now on, or `None`."""
        with self._lock:
            playing = sorted(self._playing)
        answer = self.client.ask(
            engine=self.engine.name,
            playing=playing,
            max_matches_at_once=self.max_matches_at_once,
            scala_version=__version__,
            next_window_opens=next_window_opens,
        )
        self._follow(matches_at_once=answer["matches_at_once"], paused=answer["paused"])
        match = answer["match"]
        if match is None:
            if not answer["paused"] and next_window_opens is None:
                self._note("No match to play")
            return None
        with self._lock:
            self._playing.add(match["id"])
            self._last_note = None
        return match

    def _follow(self, *, matches_at_once: int, paused: bool) -> None:
        """Takes the server's settings, and says when they change."""
        settings = (min(matches_at_once, self.max_matches_at_once), paused)
        with self._lock:
            changed, self._settings = settings != self._settings, settings
        if not changed:
            return
        if paused:
            logger.info("Paused on the server: taking no new match, and playing those being played to their end")
            return
        allowed = f"; it asks for {matches_at_once}, but this machine allows {self.max_matches_at_once}"
        logger.info(
            f"Plays up to {_matches(settings[0])} at once, as the server says"
            + (allowed if matches_at_once > self.max_matches_at_once else "")
        )

    def _may_play_another(self) -> bool:
        """Whether the server's last answer leaves room for another match beside those being played."""
        with self._lock:
            return self._settings is not None and not self._settings[1] and len(self._playing) < self._settings[0]

    def _work(self, offered: queue.SimpleQueue[dict[str, Any] | None], failures: list[Exception]) -> None:
        """One worker's matches, played one after the other as they are handed to it, until it is told to stop.

        A failure it did not foresee stops the runner, and the worker goes on taking what it is handed, each reported
        unplayed since the runner is stopping, so that no match handed over is left leased.
        """
        while (match := offered.get()) is not None:
            try:
                self._play_in_its_folder(match)
            except ServerError as error:
                logger.error(f"Match {match['id']}: telling the server it could not be played failed: {error}")
            except Exception as error:
                logger.exception(f"{threading.current_thread().name} failed")
                failures.append(error)
                self.stop()
            finally:
                self._played(match)

    def _played(self, match: dict[str, Any]) -> None:
        """Counts `match` played no more, and has the runner ask again, since a match may be free."""
        with self._lock:
            self._playing.discard(match["id"])
        self._ask_again.set()

    def _play_in_its_folder(self, offered: dict[str, Any]) -> bool:
        """Plays and reports `offered` in a folder of its own; returns whether the server took a result."""
        match_folder = self.work_folder / f"{_MATCH_FOLDER_PREFIX}{offered['id']}"
        with self._lock:
            self._match_folders_in_play.add(match_folder)
        try:
            return self._play(offered, match_folder)
        finally:
            with self._lock:
                self._match_folders_in_play.discard(match_folder)
            self._prune()

    def _play(self, offered: dict[str, Any], match_folder: Path) -> bool:
        description = f"Match {offered['id']} in {offered['ladder']}, " + " vs ".join(
            player.get("bot") or player["computer"] for player in offered["players"]
        )
        try:
            shutil.rmtree(match_folder, ignore_errors=True)
            match = self._match_from(offered, match_folder)
            if self._stopping.is_set():
                raise ScalaError("The runner was stopped before the match began")
            deadline = datetime.fromisoformat(offered["deadline"])
            longest = timedelta(seconds=match.limits.max_real_time_seconds + _START_AND_REPORT_SECONDS)
            if datetime.now(UTC) + longest > deadline:
                raise ScalaError("The match was ready too late to end by its deadline")
            logger.info(f"{description} on {offered['map']['name']}")
            result = self.engine.play(match, output_folder=match_folder / "output")
        except ScalaError as error:
            logger.error(f"{description} could not be played: {error}")
            _remove_copies(match_folder)
            self.client.report_failure(offered["id"], str(error))
            return False
        except Exception as error:
            _remove_copies(match_folder)
            with suppress(ServerError):
                self.client.report_failure(offered["id"], f"The runner failed: {type(error).__name__}")
            raise
        try:
            self.client.report_result(
                offered["id"], _result_report(result), files=_result_files(match, result, match_folder)
            )
        except ServerError as error:
            logger.error(f"{description}: {result.code.value}, which the server did not take: {error}")
            _remove_copies(match_folder)
            return False
        logger.info(f"{description}: {result.code.value}, reported")
        shutil.rmtree(match_folder, ignore_errors=True)
        return True

    def _match_from(self, offered: dict[str, Any], match_folder: Path) -> Match:
        """The match the server offered, its bots unpacked and its map named in `match_folder`; player 2 may be the
        computer."""
        first, second = offered["players"]
        player_1 = self._bot_from(first, match_folder / "bots" / "1")
        player_2 = (
            _computer_from(second) if "computer" in second else self._bot_from(second, match_folder / "bots" / "2")
        )
        map_file = match_folder / "maps" / f"{offered['map']['name']}.SC2Map"
        map_file.parent.mkdir(parents=True, exist_ok=True)
        with self._cached("maps", offered["map"]["sha256"]) as cached_map:
            shutil.copyfile(cached_map, map_file)
        return Match(
            player_1=player_1,
            player_2=player_2,
            map_file=map_file,
            limits=GameLimits(**offered["game_limits"]),
            resource_limits=ResourceLimits(**offered["resource_limits"]),
        )

    def _bot_from(self, player: dict[str, Any], seat_folder: Path) -> Bot:
        folder = seat_folder / player["bot"]
        with self._cached("bots", player["sha256"]) as archive:
            unpack_bot_archive(archive, folder, max_unpacked_bytes=_MAX_UNPACKED_BOT_BYTES)
        return Bot(name=player["bot"], race=Race(player["race"]), bot_type=BotType(player["type"]), folder=folder)

    @contextmanager
    def _cached(self, kind: str, sha256: str) -> Iterator[Path]:
        """The file of `kind` with that hash, downloaded into the cache if it is not there yet, by one worker while
        the others wait, and kept from pruning while it is used."""
        path = self.cache_folder / kind / f"{sha256}{_CACHED_SUFFIXES[kind]}"
        with self._lock:
            self._cached_files_in_use[path] += 1
            download = self._downloads.setdefault(path, threading.Lock())
        try:
            with download:
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.client.download(kind, sha256, path)
            os.utime(path)
            yield path
        finally:
            with self._lock:
                self._cached_files_in_use[path] -= 1
                if not self._cached_files_in_use[path]:
                    del self._cached_files_in_use[path]
                    del self._downloads[path]

    def _prune(self) -> None:
        """Removes the cached files least recently used while the cache holds more than its limit, and the kept match
        folders but the latest, leaving what a match is using."""
        try:
            with self._lock:
                self._prune_cache()
                pruned_folders = self._kept_match_folders_to_prune()
            for folder in pruned_folders:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError as error:
            logger.warning(f"Pruning the runner's folders failed: {error}")

    def _prune_cache(self) -> None:
        if self.cache_bytes is None:
            return
        by_use = sorted(
            (path.stat().st_mtime, path.stat().st_size, path)
            for kind, suffix in _CACHED_SUFFIXES.items()
            for path in (self.cache_folder / kind).glob(f"*{suffix}")
        )
        total = sum(size for _, size, _ in by_use)
        for _, size, path in by_use:
            if total <= self.cache_bytes:
                return
            if path not in self._cached_files_in_use:
                path.unlink(missing_ok=True)
                total -= size
                logger.info(f"Removed {path.name} from the cache, the least recently used")

    def _kept_match_folders_to_prune(self) -> list[Path]:
        """The kept match folders but the latest, none of them being played, which no match can take again since
        each match has a folder of its own."""
        kept = sorted(
            (folder for folder in self._match_folders() if folder not in self._match_folders_in_play),
            key=lambda folder: int(folder.name.removeprefix(_MATCH_FOLDER_PREFIX)),
        )
        return kept[: max(len(kept) - self.kept_match_folders, 0)]

    def remove_leftovers(self) -> None:
        """Removes what matches cut short left: their containers, if the runner was killed while they played, and
        partial downloads; and prunes the cache and the kept match folders.

        Raises `EngineError` if the engine cannot remove a match's containers, before any folder is pruned.
        """
        for folder in self._match_folders():
            self.engine.remove_leftovers(folder / "output")
        for partial in self.cache_folder.glob("*/*.partial"):
            partial.unlink(missing_ok=True)
        self._prune()

    def _match_folders(self) -> list[Path]:
        """The folders of the matches played, kept or being played."""
        if not self.work_folder.is_dir():
            return []
        return [
            folder
            for folder in self.work_folder.glob(f"{_MATCH_FOLDER_PREFIX}*")
            if folder.is_dir() and folder.name.removeprefix(_MATCH_FOLDER_PREFIX).isdigit()
        ]

    def _note(self, message: str) -> None:
        """Logs `message`, unless it is the last noted: every worker waiting notes the same."""
        with self._lock:
            if message == self._last_note:
                return
            self._last_note = message
        logger.info(message)


def _matches(count: int) -> str:
    return f"{count} {'match' if count == 1 else 'matches'}"


def _computer_from(player: dict[str, Any]) -> Computer:
    return Computer(
        race=Race(player["race"]), difficulty=Difficulty(player["difficulty"]), build=Build(player["build"])
    )


def _remove_copies(match_folder: Path) -> None:
    """Removes the bots and the map unpacked into a match folder that is kept, which the cache holds anyway."""
    for copies in ("bots", "maps"):
        shutil.rmtree(match_folder / copies, ignore_errors=True)


def _seconds_until(moment: datetime) -> float:
    """The seconds from now until `moment`, a local time with no time zone, across a change of daylight saving; one
    at least."""
    return max((moment.astimezone() - datetime.now().astimezone()).total_seconds(), 1.0)


def _result_report(result: MatchResult) -> dict[str, Any]:
    return {
        "code": result.code.value,
        "game_steps": result.game_steps,
        "average_step_times": list(result.average_step_times),
    }


def _result_files(match: Match, result: MatchResult, match_folder: Path) -> dict[str, Path]:
    """The replay, and the logs zipped: each bot's, from its folder, and the engine's, all the rest; the computer has
    none."""
    zips = match_folder / "zips"
    zips.mkdir(parents=True, exist_ok=True)
    files = {"replay": result.replay_file} if result.replay_file else {}
    bot_log_folders = []
    for seat, bot in enumerate(match.bots, start=1):
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
