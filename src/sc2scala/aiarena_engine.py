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
from sc2scala.command_runner import CommandRunner, run_command
from sc2scala.errors import EngineError, MatchTimeoutError
from sc2scala.game_limits import GameLimits
from sc2scala.match import Match
from sc2scala.match_result import MatchResult
from sc2scala.result_code import ResultCode

AIARENA_IMAGE_TAG = "v0.8.0"
"""The tag of AI Arena's images, as its local setup pins them."""

_REAL_TIME_MARGIN_SECONDS = 300
_CONTAINER_STOP_SECONDS = 10
_WORK_FOLDER_NAME = ".work"
_MATCH_CONTROLLER_SERVICE = "proxy_controller"


class AiArenaEngine:
    """Plays a match with AI Arena's match controller, in its Docker images, through Docker Compose.

    Needs Docker with Compose v2, and pulls a missing image before the match starts. Each match is a Compose project
    of its own, so that several can run at once. Each bot's controller sees its own bot's folder alone.

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
    ) -> None:
        self.image_tag = image_tag
        self.keep_work_folder = keep_work_folder
        self._run_command = run_command

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        """Plays `match` as `MatchEngine.play` says.

        Raises `MatchTimeoutError` if the match runs past its real-time limit and a margin, and `EngineError` if
        Docker is missing, a container does not start, or the engine reports no result.
        """
        _prepare_output_folder(output_folder)
        logs_folder = output_folder / "logs"
        logs_folder.mkdir()
        compose_log = logs_folder / "compose.log"
        work_folder = output_folder / _WORK_FOLDER_NAME
        compose_file = _lay_out_work_folder(match, work_folder=work_folder, image_tag=self.image_tag)
        project_name = f"sc2scala-{uuid.uuid4().hex[:12]}"
        compose = ["docker", "compose", "--project-name", project_name, "--file", str(compose_file)]

        logger.info(f"Playing {match.player_1.name} against {match.player_2.name} on {match.map_name}")
        started = time.monotonic()
        created = False
        try:
            create = [*compose, "create", "--pull", "missing"]
            create_exit_code = self._run_command(create, timeout=None, log_file=compose_log)
            created = True
            if create_exit_code != 0:
                raise EngineError(f"Docker Compose could not create the match's containers; see {compose_log}")
            started = time.monotonic()
            timeout = match.limits.max_real_time_seconds + _REAL_TIME_MARGIN_SECONDS
            up = [*compose, "up", "--abort-on-container-exit", "--exit-code-from", _MATCH_CONTROLLER_SERVICE]
            try:
                self._run_command(up, timeout=timeout, log_file=compose_log)
            except subprocess.TimeoutExpired as error:
                raise MatchTimeoutError(f"The match ran past {timeout} seconds and was stopped") from error
        finally:
            real_time_seconds = time.monotonic() - started
            if created:
                down = [*compose, "down", "--remove-orphans", "--timeout", str(_CONTAINER_STOP_SECONDS)]
                self._run_command(down, timeout=None, log_file=compose_log)
                self._hand_back_work_folder(work_folder, compose_log=compose_log)

        replay_file = _move_replay(work_folder / "replays", output_folder)
        _move_logs(match, work_folder=work_folder, logs_folder=logs_folder)
        engine_result = _read_engine_result(work_folder / "results.json", compose_log=compose_log)
        if not self.keep_work_folder:
            try:
                shutil.rmtree(work_folder)
            except OSError as error:
                logger.warning(f"Could not remove {work_folder}: {error}")
        return MatchResult(
            code=ResultCode(engine_result["type"]),
            game_steps=int(engine_result.get("game_steps", 0)),
            average_step_times=(engine_result.get("bot1_avg_step_time"), engine_result.get("bot2_avg_step_time")),
            real_time_seconds=real_time_seconds,
            replay_file=replay_file,
            logs_folder=logs_folder,
        )

    def _hand_back_work_folder(self, work_folder: Path, *, compose_log: Path) -> None:
        """Gives the files the containers wrote, as root, to this process's user, who can then move and remove them."""
        if not hasattr(os, "getuid"):
            return
        chown = [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--volume",
            f"{work_folder.resolve()}:/work",
            "--entrypoint",
            "chown",
            f"aiarena/arenaclient-bot:{self.image_tag}",
            "-R",
            f"{os.getuid()}:{os.getgid()}",
            "/work",
        ]
        if self._run_command(chown, timeout=None, log_file=compose_log) != 0:
            logger.warning(f"Could not hand back the files in {work_folder}; see {compose_log}")


def _prepare_output_folder(output_folder: Path) -> None:
    if output_folder.exists() and any(output_folder.iterdir()):
        raise ValueError(f"{output_folder} is not empty")
    output_folder.mkdir(parents=True, exist_ok=True)


def _lay_out_work_folder(match: Match, *, work_folder: Path, image_tag: str) -> Path:
    """Writes what the engine reads into `work_folder`, and returns the Compose file."""
    work_folder.mkdir()
    for seat, bot in enumerate((match.player_1, match.player_2), start=1):
        shutil.copytree(bot.folder, work_folder / "bots" / str(seat) / bot.name, symlinks=True)
        (work_folder / "logs" / f"bot_controller{seat}").mkdir(parents=True)
    (work_folder / "maps").mkdir()
    shutil.copyfile(match.map_file, work_folder / "maps" / match.map_file.name)
    (work_folder / "replays").mkdir()
    (work_folder / "matches").write_text(_matches_line(match) + "\n", encoding="utf-8")
    (work_folder / "results.json").write_text('{"results": []}\n', encoding="utf-8")
    (work_folder / "config.toml").write_text(_engine_config(match.limits), encoding="utf-8")
    compose_file = work_folder / "docker-compose.json"
    compose_file.write_text(json.dumps(_compose_project(work_folder.resolve(), image_tag), indent=2), encoding="utf-8")
    return compose_file


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


def _compose_project(work_folder: Path, image_tag: str) -> dict[str, Any]:
    """The Compose project of one match, as AI Arena's local setup lays it out, but for each bot's own folder."""

    def bot_controller(seat: int) -> dict[str, Any]:
        return {
            "image": f"aiarena/arenaclient-bot:{image_tag}",
            "environment": [f"ACBOT_PORT={8080 + seat}", f"ACBOT_PROXY_HOST={_MATCH_CONTROLLER_SERVICE}"],
            "volumes": [
                f"{work_folder}/bots/{seat}:/bots",
                f"{work_folder}/logs/bot_controller{seat}:/logs",
            ],
        }

    return {
        "services": {
            "sc2_controller": {
                "image": f"aiarena/arenaclient-sc2:{image_tag}",
                "environment": ["ACSC2_PORT=8083", f"ACSC2_PROXY_HOST={_MATCH_CONTROLLER_SERVICE}"],
                "volumes": [f"{work_folder}/logs:/logs", f"{work_folder}/maps:/root/StarCraftII/maps"],
            },
            "bot_controller1": bot_controller(1),
            "bot_controller2": bot_controller(2),
            _MATCH_CONTROLLER_SERVICE: {
                "image": f"aiarena/arenaclient-proxy:{image_tag}",
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
            },
        }
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
