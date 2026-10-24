import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from sc2scala.bot import Bot
from sc2scala.bot_type import BotType
from sc2scala.command_runner import CommandRunner, run_command
from sc2scala.errors import EngineError, MatchTimeoutError
from sc2scala.game_limits import GameLimits
from sc2scala.log_watchdog import LogWatchdog
from sc2scala.match import Match
from sc2scala.match_result import MatchResult
from sc2scala.result_code import ResultCode

AIARENA_IMAGE_TAG = "v0.8.0"
"""The tag of AI Arena's images, as its local setup pins them."""

_REAL_TIME_MARGIN_SECONDS = 300
_CONTAINER_STOP_SECONDS = 10
_WORK_FOLDER_NAME = ".work"
_MATCH_CONTROLLER_SERVICE = "proxy_controller"
_GAME_FOLDER = "/root/StarCraftII"
_MEBIBYTE = 1024 * 1024
_NETWORKS = ("player_1", "player_2", "game")
"""The match's networks: each bot's, which it shares with the match controller alone, and the game's."""


class AiArenaEngine:
    """Plays a match with AI Arena's match controller, in its Docker images, through Docker Compose.

    Needs Docker with Compose 2.20 or later, for `docker compose wait`, and pulls a missing image before the match
    starts. Each match is a Compose project of its own, so that several can run at once, and each bot plays sandboxed,
    as `docs/sandbox.md` says. Under rootless Docker, which `rootless` says or Docker is asked about at the first
    match, the containers run as their root, which is the user who started Docker; otherwise as the user of this
    process.

    The engine lays its files out in a work folder inside the output folder, and removes it once the match is over,
    leaving the replay and `logs`, which holds the controllers' logs and each bot's own, in `player_1-<name>` and
    `player_2-<name>`, and `compose.log`, what Docker Compose wrote. With `keep_work_folder`, the work folder stays.
    """

    def __init__(
        self,
        *,
        image_tag: str = AIARENA_IMAGE_TAG,
        run_command: CommandRunner = run_command,
        keep_work_folder: bool = False,
        rootless: bool | None = None,
    ) -> None:
        self.image_tag = image_tag
        self.keep_work_folder = keep_work_folder
        self.rootless = rootless
        self._run_command = run_command

    @property
    def images(self) -> list[str]:
        """The engine's Docker images, at its tag."""
        return [f"aiarena/arenaclient-{part}:{self.image_tag}" for part in ("sc2", "bot", "proxy")]

    def pull_images(self, *, log_file: Path) -> None:
        """Downloads the engine's images that Docker lacks, about 11 GB at first; raises `EngineError` if it cannot."""
        log_file.parent.mkdir(parents=True, exist_ok=True)
        for image in self.images:
            if self._run_command(["docker", "image", "inspect", image], timeout=None, log_file=log_file) == 0:
                continue
            if self._run_command(["docker", "pull", "--quiet", image], timeout=None, log_file=log_file) != 0:
                raise EngineError(f"Docker could not pull {image}; see {log_file}")

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        """Plays `match` as `MatchEngine.play` says.

        Raises `MatchTimeoutError` if the match runs past its real-time limit and a margin, and `EngineError` for a
        Windows bot, or if Docker is missing, a container does not start, or the engine reports no result.
        """
        for bot in (match.player_1, match.player_2):
            if bot.bot_type == BotType.CPP_WIN32:
                raise EngineError(f"{bot.name} is a Windows bot, and the engine's images hold no Wine to run it")
        _prepare_output_folder(output_folder)
        logs_folder = output_folder / "logs"
        logs_folder.mkdir()
        compose_log = logs_folder / "compose.log"
        work_folder = output_folder / _WORK_FOLDER_NAME
        if self.rootless is None:
            self.rootless = _docker_is_rootless()
        container_user = None if self.rootless or not hasattr(os, "getuid") else f"{os.getuid()}:{os.getgid()}"
        compose_file = _lay_out_work_folder(
            match, work_folder=work_folder, image_tag=self.image_tag, container_user=container_user
        )
        project_name = f"sc2scala-{uuid.uuid4().hex[:12]}"
        compose = ["docker", "compose", "--project-name", project_name, "--file", str(compose_file)]

        logger.info(f"Playing {match.player_1.name} against {match.player_2.name} on {match.map_name}")
        real_time_seconds, seat_over_log_limit = self._play_in_containers(
            match, compose=compose, work_folder=work_folder, compose_log=compose_log
        )
        replay_file = _move_replay(work_folder / "replays", output_folder)
        _move_logs(match, work_folder=work_folder, logs_folder=logs_folder)
        try:
            engine_result = _read_engine_result(work_folder / "results.json", compose_log=compose_log)
        except EngineError:
            if seat_over_log_limit is None:
                raise
            engine_result = {}
        if not self.keep_work_folder:
            try:
                shutil.rmtree(work_folder)
            except OSError as error:
                logger.warning(f"Could not remove {work_folder}: {error}")
        if seat_over_log_limit is None:
            code = ResultCode(engine_result["type"])
        else:
            logger.warning(f"Player {seat_over_log_limit}'s logs grew past their limit, and it was stopped")
            code = ResultCode.crash_of(seat_over_log_limit)
        return MatchResult(
            code=code,
            game_steps=int(engine_result.get("game_steps", 0)),
            average_step_times=(engine_result.get("bot1_avg_step_time"), engine_result.get("bot2_avg_step_time")),
            real_time_seconds=real_time_seconds,
            replay_file=replay_file,
            logs_folder=logs_folder,
        )

    def _play_in_containers(
        self, match: Match, *, compose: list[str], work_folder: Path, compose_log: Path
    ) -> tuple[float, int | None]:
        """Creates the match's containers, starts them, waits for the match controller to end, and removes them,
        whatever happens.

        A container that stops, a bot's killed for its logs among them, leaves the others playing. Returns the real time
        the match took, and the seat of the bot whose logs grew past their limit, or `None`.
        """
        started = time.monotonic()
        created = False
        try:
            create = [*compose, "create", "--pull", "missing"]
            create_exit_code = self._run_command(create, timeout=None, log_file=compose_log)
            created = True
            if create_exit_code != 0:
                raise EngineError(f"Docker Compose could not create the match's containers; see {compose_log}")
            started = time.monotonic()
            if self._run_command([*compose, "up", "--detach"], timeout=None, log_file=compose_log) != 0:
                raise EngineError(f"Docker Compose could not start the match's containers; see {compose_log}")
            timeout = match.limits.max_real_time_seconds + _REAL_TIME_MARGIN_SECONDS
            watchdog = LogWatchdog(
                [work_folder / "logs" / f"bot_controller{seat}" for seat in (1, 2)],
                max_bytes=match.resource_limits.bot_log_mib * _MEBIBYTE,
                stop_bot=lambda seat: self._run_command(
                    [*compose, "kill", f"bot_controller{seat}"], timeout=None, log_file=compose_log
                ),
            )
            try:
                with watchdog:
                    wait = [*compose, "wait", _MATCH_CONTROLLER_SERVICE]
                    self._run_command(wait, timeout=timeout, log_file=compose_log)
            except subprocess.TimeoutExpired as error:
                raise MatchTimeoutError(f"The match ran past {timeout} seconds and was stopped") from error
            return time.monotonic() - started, watchdog.exceeded_seat
        finally:
            if created:
                logs = [*compose, "logs", "--no-color", "--timestamps"]
                try:
                    self._run_command(logs, timeout=_CONTAINER_STOP_SECONDS * 6, log_file=compose_log)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Docker Compose took too long to write the containers' output to {compose_log}")
                down = [*compose, "down", "--remove-orphans", "--timeout", str(_CONTAINER_STOP_SECONDS)]
                self._run_command(down, timeout=None, log_file=compose_log)


def _prepare_output_folder(output_folder: Path) -> None:
    if output_folder.exists() and any(output_folder.iterdir()):
        raise ValueError(f"{output_folder} is not empty")
    output_folder.mkdir(parents=True, exist_ok=True)


def _docker_is_rootless() -> bool:
    """Whether Docker runs rootless, its daemon and its containers' root as the user who started it."""
    try:
        security_options = subprocess.run(
            ["docker", "info", "--format", "{{json .SecurityOptions}}"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise EngineError("Docker is not installed, or not running, or this user may not use it") from error
    return "name=rootless" in security_options


def _lay_out_work_folder(match: Match, *, work_folder: Path, image_tag: str, container_user: str | None) -> Path:
    """Writes what the engine reads into `work_folder`, and returns the Compose file."""
    work_folder.mkdir()
    for seat, bot in enumerate((match.player_1, match.player_2), start=1):
        bot_folder = work_folder / "bots" / str(seat) / bot.name
        shutil.copytree(bot.folder, bot_folder, symlinks=True)
        (bot_folder / "data").mkdir(exist_ok=True)
        _make_entry_point_executable(bot, bot_folder)
        (work_folder / "logs" / f"bot_controller{seat}").mkdir(parents=True)
    (work_folder / "maps").mkdir()
    shutil.copyfile(match.map_file, work_folder / "maps" / match.map_file.name)
    (work_folder / "replays").mkdir()
    (work_folder / "matches").write_text(_matches_line(match) + "\n", encoding="utf-8")
    (work_folder / "results.json").write_text('{"results": []}\n', encoding="utf-8")
    (work_folder / "config.toml").write_text(_engine_config(match.limits), encoding="utf-8")
    compose_file = work_folder / "docker-compose.json"
    compose_project = _compose_project(
        match, work_folder=work_folder.resolve(), image_tag=image_tag, user=container_user
    )
    compose_file.write_text(json.dumps(compose_project, indent=2), encoding="utf-8")
    return compose_file


def _make_entry_point_executable(bot: Bot, bot_folder: Path) -> None:
    """Lets the binary of a Linux bot run, which its controller cannot see to on a folder it may only read.

    A binary that is a symbolic link is left as it is, since its target may lie outside the work folder.
    """
    binary = bot_folder / bot.name
    if bot.bot_type in (BotType.CPP_LINUX, BotType.LINUX) and not binary.is_symlink():
        binary.chmod(binary.stat().st_mode | 0o111)


def _matches_line(match: Match) -> str:
    """The match as a line of the engine's match file, each bot's name serving as its id."""

    def fields_of(bot: Bot) -> list[str]:
        return [bot.name, bot.name, bot.race.value, bot.bot_type.value]

    return ",".join([*fields_of(match.player_1), *fields_of(match.player_2), match.map_name])


def _engine_config(limits: GameLimits) -> str:
    """The match controller's `config.toml`, as AI Arena's local setup writes it, with `limits`."""
    return f"""\
ARENA_CLIENT_ID = "sc2scala"
ROUNDS_PER_RUN = -1
BASE_WEBSITE_URL = ""
DEBUG_MODE = false
PYTHON = "python"
RUN_TYPE = "local"
SECURE_MODE = false

LOGGING_LEVEL = "info"
LOG_ROOT = "/logs"

REPLAYS_DIRECTORY = "/replays"
BOTS_DIRECTORY = "/bots"

MAX_GAME_TIME = {limits.max_game_steps}
MAX_REAL_TIME = {limits.max_real_time_seconds}
MAX_FRAME_TIME = {limits.max_step_time_ms}
STRIKES = {limits.step_time_strikes}
REALTIME = false
VISUALIZE = false

DISABLE_DEBUG = true
VALIDATE_RACE = false

MATCHES_FILE = "matches"
RESULTS_FILE = "results.json"

BOT_CONT_1_HOST = "127.0.0.1"
BOT_CONT_1_PORT = 8081
BOT_CONT_2_HOST = "127.0.0.1"
BOT_CONT_2_PORT = 8082
SC2_CONT_HOST = "127.0.0.1"
SC2_CONT_PORT = 8083
"""


def _compose_project(match: Match, *, work_folder: Path, image_tag: str, user: str | None) -> dict[str, Any]:
    """The Compose project of one match, as AI Arena's local setup lays it out, with each container sandboxed.

    Every container runs as `user`, if given, with no capabilities and no way to gain privileges. Each bot's
    controller sees its own bot alone, read-only but for its `data` folder and `/tmp`, which are held in memory, and
    reaches the match controller alone, on a network of its own. The game reaches it on another, and no network
    reaches beyond the match.
    """
    resource_limits = match.resource_limits
    unprivileged: dict[str, Any] = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
    if user is not None:
        unprivileged["user"] = user

    def bot_controller(seat: int, bot: Bot) -> dict[str, Any]:
        memory = f"{resource_limits.bot_memory_mib}m"
        return {
            "image": f"aiarena/arenaclient-bot:{image_tag}",
            **unprivileged,
            "environment": [
                f"ACBOT_PORT={8080 + seat}",
                f"ACBOT_PROXY_HOST={_MATCH_CONTROLLER_SERVICE}",
                "HOME=/tmp",
            ],
            "volumes": [
                f"{work_folder}/bots/{seat}:/bots:ro",
                f"{work_folder}/logs/bot_controller{seat}:/logs",
            ],
            "read_only": True,
            "tmpfs": [f"/tmp:size={memory},mode=1777", f"/bots/{bot.name}/data:size={memory},mode=1777"],
            "mem_limit": memory,
            "memswap_limit": memory,
            "cpus": resource_limits.bot_cpus,
            "pids_limit": resource_limits.bot_processes,
            "networks": [_NETWORKS[seat - 1]],
        }

    game_memory = f"{resource_limits.game_memory_mib}m"
    match_controller_memory = f"{resource_limits.match_controller_memory_mib}m"
    return {
        "services": {
            "sc2_controller": {
                "image": f"aiarena/arenaclient-sc2:{image_tag}",
                **unprivileged,
                "environment": [
                    "ACSC2_PORT=8083",
                    f"ACSC2_PROXY_HOST={_MATCH_CONTROLLER_SERVICE}",
                    f"SC2PATH={_GAME_FOLDER}",
                    "HOME=/root",
                ],
                "volumes": [f"{work_folder}/logs:/logs", f"{work_folder}/maps:{_GAME_FOLDER}/maps"],
                "mem_limit": game_memory,
                "memswap_limit": game_memory,
                "cpus": resource_limits.game_cpus,
                "networks": [_NETWORKS[2]],
            },
            "bot_controller1": bot_controller(1, match.player_1),
            "bot_controller2": bot_controller(2, match.player_2),
            _MATCH_CONTROLLER_SERVICE: {
                "image": f"aiarena/arenaclient-proxy:{image_tag}",
                **unprivileged,
                "environment": [
                    "ACPROXY_PORT=8080",
                    "ACPROXY_BOT_CONT_1_HOST=bot_controller1",
                    "ACPROXY_BOT_CONT_2_HOST=bot_controller2",
                    "ACPROXY_SC2_CONT_HOST=sc2_controller",
                ],
                "volumes": [
                    f"{work_folder}/matches:/app/matches",
                    f"{work_folder}/config.toml:/app/config.toml",
                    f"{work_folder}/results.json:/app/results.json",
                    f"{work_folder}/replays:/replays",
                    f"{work_folder}/logs:/logs",
                ],
                "mem_limit": match_controller_memory,
                "memswap_limit": match_controller_memory,
                "cpus": resource_limits.match_controller_cpus,
                "networks": list(_NETWORKS),
            },
        },
        "networks": {network: {"internal": True} for network in _NETWORKS},
    }


def _read_engine_result(results_file: Path, *, compose_log: Path) -> dict[str, Any]:
    """The one result the engine wrote, as it wrote it.

    Raises `EngineError` if it wrote none, or one whose `type` is not a `ResultCode`.
    """
    try:
        results = json.loads(results_file.read_text(encoding="utf-8")).get("results", [])
    except (OSError, ValueError) as error:
        raise EngineError(f"The engine's results could not be read; see {compose_log}") from error
    if not results:
        raise EngineError(f"The engine reported no result; see {compose_log}")
    engine_result = results[-1]
    if engine_result.get("type") not in set(ResultCode):
        raise EngineError(f"The engine reported a result of unknown type: {engine_result}")
    return engine_result


def _move_replay(replays_folder: Path, output_folder: Path) -> Path | None:
    replays = sorted(replays_folder.glob("*.SC2Replay"))
    if not replays:
        logger.warning("The engine saved no replay")
        return None
    return Path(shutil.move(replays[0], output_folder / replays[0].name))


def _move_logs(match: Match, *, work_folder: Path, logs_folder: Path) -> None:
    """Moves the controllers' logs into `logs_folder`, and each bot's own as `player_<seat>-<name>`."""
    engine_logs_folder = work_folder / "logs"
    for seat, bot in enumerate((match.player_1, match.player_2), start=1):
        bot_logs = engine_logs_folder / f"bot_controller{seat}" / bot.name
        if bot_logs.is_dir():
            shutil.move(bot_logs, logs_folder / f"player_{seat}-{bot.name}")
    for engine_logs in engine_logs_folder.iterdir():
        shutil.move(engine_logs, logs_folder / engine_logs.name)
