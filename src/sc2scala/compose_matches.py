"""What Scala's engines share: each match a Docker Compose project of its own, laid out in a work folder inside the
match's output folder, its containers created, started, waited for and removed, and stopped all at once."""

import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from sc2scala.bot import Bot
from sc2scala.bot_type import BotType
from sc2scala.command_runner import CommandRunner
from sc2scala.errors import EngineError, MatchStoppedError, MatchTimeoutError
from sc2scala.log_watchdog import LogWatchdog
from sc2scala.match import Match

WORK_FOLDER_NAME = ".work"
COMPOSE_FILE_NAME = "docker-compose.json"
_PROJECT_FILE_NAME = "compose-project"
"""The file in the work folder that names the match's Compose project while its containers may exist."""
_REAL_TIME_MARGIN_SECONDS = 300
_CONTAINER_STOP_SECONDS = 10
_MEBIBYTE = 1024 * 1024
UNPRIVILEGED_OPTIONS = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
"""What every container of a match runs with: no capabilities, and no way to gain privileges."""


class ComposeMatches:
    """Plays matches as Docker Compose projects, each its own, so that several can run at once, from several threads,
    and `stop_matches` stops them all.

    Needs Docker with Compose 2.20 or later, for `docker compose wait`, and pulls a missing image before a match starts.
    """

    def __init__(self, run_command: CommandRunner) -> None:
        self._run_command = run_command
        self._lock = threading.Lock()
        self._stopped = False
        self._playing: dict[tuple[str, ...], Path] = {}
        """The Compose command of each match being played, with its log."""

    def pull_images(self, images: Sequence[str], *, log_file: Path) -> None:
        """Downloads the `images` Docker lacks; raises `EngineError` if it cannot."""
        log_file.parent.mkdir(parents=True, exist_ok=True)
        for image in images:
            if self._run_command(["docker", "image", "inspect", image], timeout=None, log_file=log_file) == 0:
                continue
            if self._run_command(["docker", "pull", "--quiet", image], timeout=None, log_file=log_file) != 0:
                raise EngineError(f"Docker could not pull {image}; see {log_file}")

    def stop_matches(self) -> None:
        """Kills the containers of the matches being played, which then raise `MatchStoppedError`, and refuses to play
        any later."""
        with self._lock:
            self._stopped = True
            playing = list(self._playing.items())
        for compose, compose_log in playing:
            try:
                self._run_command([*compose, "kill"], timeout=_CONTAINER_STOP_SECONDS * 6, log_file=compose_log)
            except (EngineError, subprocess.TimeoutExpired) as error:
                logger.warning(f"Docker Compose could not kill a match's containers: {error}")

    def remove_leftovers(self, output_folder: Path) -> None:
        """Removes the containers of a match played into `output_folder`, if what played it was killed before it
        could, which the work folder then still names; raises `EngineError` if Docker Compose cannot."""
        project_file = output_folder / WORK_FOLDER_NAME / _PROJECT_FILE_NAME
        if not project_file.is_file():
            return
        project_name = project_file.read_text(encoding="utf-8").strip()
        logger.info(f"Removing the containers of {project_name}, which a match left in {output_folder}")
        log_file = output_folder / "logs" / "compose.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        down = ["docker", "compose", "--project-name", project_name, "down", "--remove-orphans"]
        if self._run_command([*down, "--timeout", str(_CONTAINER_STOP_SECONDS)], timeout=None, log_file=log_file) != 0:
            raise EngineError(f"Docker Compose could not remove the containers of {project_name}; see {log_file}")
        project_file.unlink()

    def play_in_containers(
        self,
        match: Match,
        *,
        work_folder: Path,
        compose_log: Path,
        controller_service: str,
        bot_services: tuple[str, ...],
        bot_log_folders: tuple[Path, ...],
    ) -> tuple[float, int | None]:
        """Creates the containers of the Compose file in `work_folder`, starts them, waits for `controller_service`
        to end, and removes them, whatever happens.

        A container that stops, a bot's killed for its logs among them, leaves the others playing: a bot whose folder
        in `bot_log_folders` grows past the match's limit is killed, its service named in `bot_services`. Returns the
        real time the match took, and the seat of the bot whose logs grew past their limit, or `None`. Raises
        `MatchTimeoutError` if the match runs past its real-time limit and a margin, and, after each step,
        `MatchStoppedError` if `stop_matches` was called meanwhile. The work folder names the project while its
        containers may exist, for `remove_leftovers`.
        """
        project_name = f"sc2scala-{uuid.uuid4().hex[:12]}"
        compose = ["docker", "compose", "--project-name", project_name, "--file", str(work_folder / COMPOSE_FILE_NAME)]
        started = time.monotonic()
        created = False
        project_file = work_folder / _PROJECT_FILE_NAME
        with self._lock:
            if self._stopped:
                raise MatchStoppedError("The match was stopped before it began")
            self._playing[tuple(compose)] = compose_log
        project_file.write_text(project_name + "\n", encoding="utf-8")
        removed = False
        try:
            create = [*compose, "create", "--pull", "missing"]
            create_exit_code = self._run_command(create, timeout=None, log_file=compose_log)
            created = True
            if create_exit_code != 0:
                raise EngineError(f"Docker Compose could not create the match's containers; see {compose_log}")
            self._raise_if_stopped()
            started = time.monotonic()
            if self._run_command([*compose, "up", "--detach"], timeout=None, log_file=compose_log) != 0:
                raise EngineError(f"Docker Compose could not start the match's containers; see {compose_log}")
            self._raise_if_stopped()
            timeout = match.limits.max_real_time_seconds + _REAL_TIME_MARGIN_SECONDS
            watchdog = LogWatchdog(
                bot_log_folders,
                max_bytes=match.resource_limits.bot_log_mib * _MEBIBYTE,
                stop_bot=lambda seat: self._run_command(
                    [*compose, "kill", bot_services[seat - 1]], timeout=None, log_file=compose_log
                ),
            )
            try:
                with watchdog:
                    self._run_command([*compose, "wait", controller_service], timeout=timeout, log_file=compose_log)
            except subprocess.TimeoutExpired as error:
                raise MatchTimeoutError(f"The match ran past {timeout} seconds and was stopped") from error
            self._raise_if_stopped()
            return time.monotonic() - started, watchdog.exceeded_seat
        finally:
            if created:
                logs = [*compose, "logs", "--no-color", "--timestamps"]
                try:
                    self._run_command(logs, timeout=_CONTAINER_STOP_SECONDS * 6, log_file=compose_log)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Docker Compose took too long to write the containers' output to {compose_log}")
                down = [*compose, "down", "--remove-orphans", "--timeout", str(_CONTAINER_STOP_SECONDS)]
                removed = self._run_command(down, timeout=None, log_file=compose_log) == 0
            if removed or not created:
                project_file.unlink()
            with self._lock:
                self._playing.pop(tuple(compose), None)

    def _raise_if_stopped(self) -> None:
        with self._lock:
            if self._stopped:
                raise MatchStoppedError("The match was stopped while it was played")


def prepare_output_folder(output_folder: Path) -> None:
    """Creates `output_folder`; raises `ValueError` if it exists and holds anything."""
    if output_folder.exists() and any(output_folder.iterdir()):
        raise ValueError(f"{output_folder} is not empty")
    output_folder.mkdir(parents=True, exist_ok=True)


def container_user(rootless: bool) -> str | None:
    """The user a match's containers run as: this process's, but under rootless Docker their root, which is the user
    who started Docker."""
    return None if rootless or not hasattr(os, "getuid") else f"{os.getuid()}:{os.getgid()}"


def docker_is_rootless() -> bool:
    """Whether Docker runs rootless, its daemon and its containers' root as the user who started it."""
    try:
        security_options = subprocess.run(
            ["docker", "info", "--format", "{{json .SecurityOptions}}"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise EngineError("Docker is not installed, or not running, or this user may not use it") from error
    return "name=rootless" in security_options


def refuse_windows_bots(match: Match) -> None:
    """Raises `EngineError` for a Windows bot, which the engines' images hold no Wine to run."""
    for bot in match.bots:
        if bot.bot_type == BotType.CPP_WIN32:
            raise EngineError(f"{bot.name} is a Windows bot, and the engine's images hold no Wine to run it")


def copy_bot(bot: Bot, bot_folder: Path) -> None:
    """Copies `bot` into `bot_folder`, with a `data` folder to mount its own over, and its binary executable."""
    shutil.copytree(bot.folder, bot_folder, symlinks=True)
    (bot_folder / "data").mkdir(exist_ok=True)
    _make_entry_point_executable(bot, bot_folder)


def _make_entry_point_executable(bot: Bot, bot_folder: Path) -> None:
    """Lets the binary of a Linux bot run, which it could not be made to on a folder its container may only read.

    A binary that is a symbolic link is left as it is, since its target may lie outside the work folder.
    """
    binary = bot_folder / bot.name
    if bot.bot_type in (BotType.CPP_LINUX, BotType.LINUX) and not binary.is_symlink():
        binary.chmod(binary.stat().st_mode | 0o111)


def move_replay(replays_folder: Path, output_folder: Path) -> Path | None:
    """Moves the replay in `replays_folder` into `output_folder` and returns it, or `None` if there is none."""
    replays = sorted(replays_folder.glob("*.SC2Replay"))
    if not replays:
        logger.warning("The engine saved no replay")
        return None
    return Path(shutil.move(replays[0], output_folder / replays[0].name))


def remove_work_folder(work_folder: Path, *, failed: bool) -> None:
    """Removes the work folder after a match, but only the copies of the bots and the map after one that failed, so
    that its logs and Compose file stay to look at."""
    if failed:
        for copies in ("bots", "maps"):
            shutil.rmtree(work_folder / copies, ignore_errors=True)
        return
    try:
        shutil.rmtree(work_folder)
    except OSError as error:
        logger.warning(f"Could not remove {work_folder}: {error}")
