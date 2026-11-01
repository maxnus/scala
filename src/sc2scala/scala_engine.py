import importlib.util
import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

import sc2scala.match_controller
from sc2scala.bot import Bot
from sc2scala.bot_type import BotType
from sc2scala.command_runner import CommandRunner, run_command
from sc2scala.compose_matches import (
    COMPOSE_FILE_NAME,
    UNPRIVILEGED_OPTIONS,
    WORK_FOLDER_NAME,
    ComposeMatches,
    container_user,
    copy_bot,
    docker_is_rootless,
    move_replay,
    prepare_output_folder,
    refuse_windows_bots,
    remove_work_folder,
)
from sc2scala.computer import Computer
from sc2scala.errors import EngineError
from sc2scala.match import Match
from sc2scala.match_controller.controller_config import ComputerConfig, ControllerConfig, SeatConfig
from sc2scala.match_result import MatchResult
from sc2scala.result_code import ResultCode

SCALA_GAME_IMAGE = "aiarena/arenaclient-sc2:v0.8.0"
"""AI Arena's image of the game, StarCraft II 4.10 for Linux, whose own controller Scala's engine leaves unused."""
SCALA_BOT_IMAGE = "aiarena/arenaclient-bot-base:v0.8.0"
"""AI Arena's bot environment, in which the bots and the match controller run."""

_CONTROLLER_SERVICE = "match_controller"
_BOT_SERVICES = ("bot_1", "bot_2")
_GAME_SERVICE = "game"
_GAME_FOLDER = "/root/StarCraftII"
_GAME_API_PORTS = (5001, 5002)
_BOT_PORTS = (8081, 8082)
"""The ports the match controller listens on for each seat's bot, given to the bot as AI Arena gives them."""
_BOT_NETWORKS = ("player_1", "player_2")
"""Each bot's network, which it shares with the match controller alone."""
_GAME_NETWORK = "game"
_CONTROLLER_PACKAGES = ("websockets", "s2clientprotocol", "google.protobuf", "google._upb")
"""What the match controller imports beside the standard library, copied beside it; `google._upb` is protobuf's
compiled part, which an install of protobuf in pure Python lacks."""
_REPLAY_FOLDER_IN_CONTAINER = "/output"
_CONTROLLER_LOG_NAME = "match_controller.log"


class ScalaEngine:
    """Plays a match with Scala's own match controller, in AI Arena's images of the game and of its bot environment,
    through Docker Compose, as `docs/plan.md` says under "The engine".

    It plays a match as `AiArenaEngine` does, each in a Compose project of its own, and each bot sandboxed as
    `docs/sandbox.md` says, and leaves the same: the replay, and `logs`, with each bot's in `player_1-<name>` and
    `player_2-<name>`, `game`, the game clients' output, `match_controller`, and `compose.log`, what Docker Compose
    wrote. Against the built-in computer, the game has one client and one bot. With `keep_work_folder`, the work folder
    stays. Under rootless Docker, which `rootless` says or Docker is asked about at the first match, the containers run
    as their root, which is the user who started Docker; otherwise as the user of this process.

    The match controller runs from copies of its package and of the packages it imports, taken from this process's,
    and mounted read-only: nothing else of this machine's reaches its container, which every bot reaches.
    """

    name = "scala"

    def __init__(
        self,
        *,
        game_image: str = SCALA_GAME_IMAGE,
        bot_image: str = SCALA_BOT_IMAGE,
        run_command: CommandRunner = run_command,
        keep_work_folder: bool = False,
        rootless: bool | None = None,
    ) -> None:
        self.game_image = game_image
        self.bot_image = bot_image
        self.keep_work_folder = keep_work_folder
        self.rootless = rootless
        self._compose = ComposeMatches(run_command)

    @property
    def images(self) -> list[str]:
        """The engine's Docker images."""
        return [self.game_image, self.bot_image]

    def pull_images(self, *, log_file: Path) -> None:
        """Downloads the engine's images that Docker lacks; raises `EngineError` if it cannot."""
        self._compose.pull_images(self.images, log_file=log_file)

    def stop_matches(self) -> None:
        """Kills the containers of the matches being played, which then raise `MatchStoppedError`, and refuses to play
        any later."""
        self._compose.stop_matches()

    def remove_leftovers(self, output_folder: Path) -> None:
        """Removes the containers of a match played into `output_folder`, if what played it was killed before it
        could; raises `EngineError` if Docker Compose cannot."""
        self._compose.remove_leftovers(output_folder)

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        """Plays `match` as `MatchEngine.play` says.

        Raises `MatchTimeoutError` if the match runs past its real-time limit and a margin, `MatchStoppedError` if
        `stop_matches` stopped it, and `EngineError` for a Windows bot, or if Docker is missing, a container does not
        start, or the match controller reports no result. A match that raises leaves its work folder, but for its
        copies of the bots and the map.
        """
        refuse_windows_bots(match)
        prepare_output_folder(output_folder)
        logs_folder = output_folder / "logs"
        logs_folder.mkdir()
        compose_log = logs_folder / "compose.log"
        work_folder = output_folder / WORK_FOLDER_NAME
        if self.rootless is None:
            self.rootless = docker_is_rootless()
        self._lay_out_work_folder(match, work_folder=work_folder, user=container_user(self.rootless))

        logger.info(f"Playing {match.player_1.name} against {match.player_2.name} on {match.map_name}")
        seats = range(len(match.bots))
        try:
            real_time_seconds, seat_over_log_limit = self._compose.play_in_containers(
                match,
                work_folder=work_folder,
                compose_log=compose_log,
                controller_service=_CONTROLLER_SERVICE,
                bot_services=tuple(_BOT_SERVICES[index] for index in seats),
                bot_log_folders=tuple(work_folder / "logs" / _BOT_NETWORKS[index] for index in seats),
            )
            replay_file = move_replay(work_folder / "output", output_folder)
            _move_logs(match, work_folder=work_folder, logs_folder=logs_folder)
            try:
                controller_result = _read_controller_result(work_folder / "output" / "result.json", logs_folder)
            except EngineError:
                if seat_over_log_limit is None:
                    raise
                controller_result = {}
        except Exception:
            if not self.keep_work_folder:
                remove_work_folder(work_folder, failed=True)
            raise
        if not self.keep_work_folder:
            remove_work_folder(work_folder, failed=False)
        if seat_over_log_limit is None:
            code = ResultCode(controller_result["outcome"])
        else:
            logger.warning(f"Player {seat_over_log_limit}'s logs grew past their limit, and it was stopped")
            code = ResultCode.crash_of(seat_over_log_limit)
        first_step_time, second_step_time = controller_result.get("average_step_times", [None, None])
        return MatchResult(
            code=code,
            game_steps=int(controller_result.get("game_steps", 0)),
            average_step_times=(first_step_time, second_step_time),
            real_time_seconds=real_time_seconds,
            replay_file=replay_file,
            logs_folder=logs_folder,
        )

    def _lay_out_work_folder(self, match: Match, *, work_folder: Path, user: str | None) -> None:
        """Writes what the match's containers read into `work_folder`, with the Compose file."""
        work_folder.mkdir()
        for seat, bot in enumerate(match.bots, start=1):
            copy_bot(bot, work_folder / "bots" / str(seat) / bot.name)
            (work_folder / "logs" / f"player_{seat}").mkdir(parents=True)
        for logs in ("game", _CONTROLLER_SERVICE):
            (work_folder / "logs" / logs).mkdir(parents=True)
        (work_folder / "maps").mkdir()
        shutil.copyfile(match.map_file, work_folder / "maps" / match.map_file.name)
        (work_folder / "output").mkdir()
        controller_folder = work_folder / "controller"
        _copy_match_controller(controller_folder)
        config = _controller_config(match)
        (controller_folder / "match.json").write_text(json.dumps(config.to_json(), indent=2), encoding="utf-8")
        compose_project = _compose_project(
            match, work_folder=work_folder.resolve(), game_image=self.game_image, bot_image=self.bot_image, user=user
        )
        (work_folder / COMPOSE_FILE_NAME).write_text(json.dumps(compose_project, indent=2), encoding="utf-8")


def _copy_match_controller(controller_folder: Path) -> None:
    """Copies the match controller's package into `controller_folder`, and the packages it imports beside it."""
    ignored = shutil.ignore_patterns("__pycache__")
    shutil.copytree(
        Path(sc2scala.match_controller.__file__).parent, controller_folder / "match_controller", ignore=ignored
    )
    for package in _CONTROLLER_PACKAGES:
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            if package == "google._upb":
                continue
            raise EngineError(f"The match controller needs {package}, which is not installed")
        source = Path(next(iter(spec.submodule_search_locations)))
        shutil.copytree(source, controller_folder.joinpath(*package.split(".")), ignore=ignored)


def _controller_config(match: Match) -> ControllerConfig:
    def seat(index: int, bot: Bot) -> SeatConfig:
        return SeatConfig(
            name=bot.name,
            race=bot.race.value,
            game_url=f"ws://{_GAME_SERVICE}:{_GAME_API_PORTS[index]}/sc2api",
            listen_port=_BOT_PORTS[index],
            bot_host=_BOT_SERVICES[index],
        )

    limits = match.limits
    computer = match.computer
    return ControllerConfig(
        map_path=f"{_GAME_FOLDER}/maps/{match.map_file.name}",
        seats=(seat(0, match.player_1),) if computer else (seat(0, match.player_1), seat(1, match.bots[1])),
        computer=_computer_config(computer) if computer else None,
        replay_file=f"{_REPLAY_FOLDER_IN_CONTAINER}/{match.player_1.name}-vs-{match.player_2.name}.SC2Replay",
        result_file=f"{_REPLAY_FOLDER_IN_CONTAINER}/result.json",
        max_game_steps=limits.max_game_steps,
        max_step_time_ms=limits.max_step_time_ms,
        step_time_strikes=limits.step_time_strikes,
        log_file=f"/logs/{_CONTROLLER_LOG_NAME}",
    )


def _computer_config(computer: Computer) -> ComputerConfig:
    return ComputerConfig(
        name=computer.name, race=computer.race.value, difficulty=computer.difficulty.value, build=computer.build.value
    )


def _game_script(clients: int) -> str:
    """What the game's container runs: `clients` game clients, one for each bot's seat, listening for the match
    controller, which alone reaches the game's network, each client's output in its log."""
    commands = [
        f'mkdir -p /tmp/client-{seat} && "$client" -listen 0.0.0.0 -port {port} -dataDir {_GAME_FOLDER} '
        f"-displayMode 0 -tempDir /tmp/client-{seat} >/logs/client-{seat}.log 2>&1 &"
        for seat, port in enumerate(_GAME_API_PORTS[:clients], start=1)
    ]
    return "\n".join(
        [f"client=$(ls -d {_GAME_FOLDER}/Versions/Base*/SC2_x64 | sort -V | tail -n 1)", *commands, "wait"]
    )


def _bot_command(bot: Bot) -> list[str]:
    """How a bot of its type starts, as AI Arena starts it, in its folder."""
    match bot.bot_type:
        case BotType.PYTHON:
            return ["python", "run.py"]
        case BotType.DOTNET_CORE:
            return ["dotnet", f"{bot.name}.dll"]
        case BotType.JAVA:
            return ["java", "-jar", f"{bot.name}.jar"]
        case BotType.NODEJS:
            return ["node", f"{bot.name}.js"]
        case BotType.CPP_LINUX | BotType.LINUX:
            return [f"./{bot.name}"]
        case BotType.CPP_WIN32:
            raise EngineError(f"{bot.name} is a Windows bot, and the engine's images hold no Wine to run it")


def _bot_script(bot: Bot, *, opponent_name: str, port: int) -> str:
    """What a bot's container runs: the bot, once the match controller listens, with the arguments AI Arena gives it,
    `--LadderServer` the controller's address and `--OpponentId` the opponent's name, a bot's or the computer's, and
    its output in its log."""
    before_address = shlex.join([*_bot_command(bot), "--GamePort", str(port), "--LadderServer"])
    after_address = shlex.join(["--StartPort", str(port), "--OpponentId", opponent_name])
    return "\n".join(
        [
            f"until controller=$(getent hosts {_CONTROLLER_SERVICE} | cut -d ' ' -f 1) && [ -n \"$controller\" ] "
            f'&& (: <>/dev/tcp/"$controller"/{port}) 2>/dev/null; do sleep 0.2; done',
            f'exec {before_address} "$controller" {after_address} >>/logs/stderr.log 2>&1',
        ]
    )


def _compose_project(
    match: Match, *, work_folder: Path, game_image: str, bot_image: str, user: str | None
) -> dict[str, Any]:
    """The Compose project of one match, with each container sandboxed.

    Every container runs as `user`, if given, with no capabilities and no way to gain privileges, and under an init
    that passes signals on. Each bot sees its own copy alone, read-only but for its `data` folder and `/tmp`, which are
    held in memory, and reaches the match controller alone, on a network of its own. The game reaches it on another,
    and no network reaches beyond the match. Against the computer, the game runs one client, and there is one bot.
    """
    resource_limits = match.resource_limits
    unprivileged: dict[str, Any] = {**UNPRIVILEGED_OPTIONS, "init": True}
    if user is not None:
        unprivileged["user"] = user
    bots = match.bots
    networks = [*_BOT_NETWORKS[: len(bots)], _GAME_NETWORK]

    def bot_service(index: int) -> dict[str, Any]:
        bot = bots[index]
        opponent_name = match.player_2.name if index == 0 else match.player_1.name
        memory = f"{resource_limits.bot_memory_mib}m"
        return {
            "image": bot_image,
            **unprivileged,
            "entrypoint": [
                "bash",
                "-c",
                _for_compose(_bot_script(bot, opponent_name=opponent_name, port=_BOT_PORTS[index])),
            ],
            "working_dir": f"/bots/{bot.name}",
            "environment": ["HOME=/tmp"],
            "volumes": [f"{work_folder}/bots/{index + 1}:/bots:ro", f"{work_folder}/logs/player_{index + 1}:/logs"],
            "read_only": True,
            "tmpfs": [f"/tmp:size={memory},mode=1777", f"/bots/{bot.name}/data:size={memory},mode=1777"],
            "mem_limit": memory,
            "memswap_limit": memory,
            "cpus": resource_limits.bot_cpus,
            "pids_limit": resource_limits.bot_processes,
            "networks": [_BOT_NETWORKS[index]],
            "depends_on": [_CONTROLLER_SERVICE],
        }

    game_memory = f"{resource_limits.game_memory_mib}m"
    controller_memory = f"{resource_limits.match_controller_memory_mib}m"
    return {
        "services": {
            _GAME_SERVICE: {
                "image": game_image,
                **unprivileged,
                "entrypoint": ["bash", "-c", _for_compose(_game_script(len(bots)))],
                "environment": ["HOME=/root"],
                "volumes": [f"{work_folder}/maps:{_GAME_FOLDER}/maps:ro", f"{work_folder}/logs/game:/logs"],
                "mem_limit": game_memory,
                "memswap_limit": game_memory,
                "cpus": resource_limits.game_cpus,
                "networks": [_GAME_NETWORK],
            },
            **{_BOT_SERVICES[index]: bot_service(index) for index in range(len(bots))},
            _CONTROLLER_SERVICE: {
                "image": bot_image,
                **unprivileged,
                "entrypoint": ["python", "-s", "-m", "match_controller", "/controller/match.json"],
                "working_dir": "/controller",
                "environment": ["PYTHONPATH=/controller", "PYTHONDONTWRITEBYTECODE=1", "HOME=/tmp"],
                "volumes": [
                    f"{work_folder}/controller:/controller:ro",
                    f"{work_folder}/output:{_REPLAY_FOLDER_IN_CONTAINER}",
                    f"{work_folder}/logs/{_CONTROLLER_SERVICE}:/logs",
                ],
                "read_only": True,
                "tmpfs": ["/tmp:size=64m,mode=1777"],
                "mem_limit": controller_memory,
                "memswap_limit": controller_memory,
                "cpus": resource_limits.match_controller_cpus,
                "networks": networks,
            },
        },
        "networks": {network: {"internal": True} for network in networks},
    }


def _for_compose(script: str) -> str:
    """`script` as a Compose file holds it, each `$` doubled, so that Compose does not take its variables for its
    own."""
    return script.replace("$", "$$")


def _read_controller_result(result_file: Path, logs_folder: Path) -> dict[str, Any]:
    """The result the match controller wrote; raises `EngineError` if it wrote none, or one of an unknown outcome."""
    log = logs_folder / _CONTROLLER_SERVICE / _CONTROLLER_LOG_NAME
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EngineError(f"The match controller reported no result; see {log}") from error
    if result.get("outcome") not in set(ResultCode):
        raise EngineError(f"The match controller reported an unknown outcome: {result}")
    return result


def _move_logs(match: Match, *, work_folder: Path, logs_folder: Path) -> None:
    """Moves the logs into `logs_folder`, each bot's as `player_<seat>-<name>`."""
    for seat, bot in enumerate(match.bots, start=1):
        shutil.move(work_folder / "logs" / f"player_{seat}", logs_folder / f"player_{seat}-{bot.name}")
    for logs in ("game", _CONTROLLER_SERVICE):
        shutil.move(work_folder / "logs" / logs, logs_folder / logs)
