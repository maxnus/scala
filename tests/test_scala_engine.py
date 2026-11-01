import json
import os
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sc2scala import (
    Bot,
    BotType,
    EngineError,
    GameLimits,
    Match,
    MatchStoppedError,
    MatchTimeoutError,
    Race,
    ResourceLimits,
    ResultCode,
    ScalaEngine,
)
from sc2scala.match_controller.controller_config import ControllerConfig
from tests.bot_folders import make_map, make_python_bot

_RESULT_OF_A_WIN = {"outcome": "Player1Win", "game_steps": 4480, "average_step_times": [0.002, None]}
_MEBIBYTE = 1024 * 1024


class FakeDocker:
    """Stands in for `docker`, recording each command and writing, while `wait` waits, what the match controller
    and the bots write.

    With `player_1_log_bytes`, player 1's log grows to that size during `wait`, which then waits for player 1 to be
    killed. `create` exits with `create_exit_code`, and `wait` times out with `wait_times_out`.
    """

    def __init__(
        self,
        *,
        controller_result: dict[str, Any] | None = None,
        player_1_log_bytes: int = 0,
        create_exit_code: int = 0,
        wait_times_out: bool = False,
    ) -> None:
        self.controller_result = controller_result
        self.player_1_log_bytes = player_1_log_bytes
        self.create_exit_code = create_exit_code
        self.wait_times_out = wait_times_out
        self.commands: list[list[str]] = []

    def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
        self.commands.append(list(args))
        if "create" in args:
            return self.create_exit_code
        if "wait" in args:
            if self.wait_times_out:
                raise subprocess.TimeoutExpired(list(args), timeout or 0)
            self._write_what_the_match_writes(Path(args[args.index("--file") + 1]).parent)
        return 0

    def subcommands(self) -> list[str]:
        return [command[6] for command in self.commands]

    def _write_what_the_match_writes(self, work_folder: Path) -> None:
        for seat in (1, 2):
            (work_folder / "logs" / f"player_{seat}" / "stderr.log").write_text(f"seat {seat} played", encoding="utf-8")
        (work_folder / "logs" / "game" / "client-1.log").write_text("the game", encoding="utf-8")
        if self.player_1_log_bytes:
            (work_folder / "logs" / "player_1" / "flood.log").write_bytes(b"x" * self.player_1_log_bytes)
            deadline = time.monotonic() + 10
            while ["kill", "bot_1"] not in [command[6:] for command in self.commands] and time.monotonic() < deadline:
                time.sleep(0.05)
            return
        if self.controller_result is not None:
            (work_folder / "output" / "result.json").write_text(json.dumps(self.controller_result), encoding="utf-8")
        (work_folder / "output" / "basic_bot-vs-loser_bot.SC2Replay").write_bytes(b"a replay")
        (work_folder / "logs" / "match_controller" / "match_controller.log").write_text("ok", encoding="utf-8")


def engine_with(docker: FakeDocker, **options: Any) -> ScalaEngine:
    return ScalaEngine(run_command=docker, rootless=False, **options)


def compose_project(output_folder: Path) -> dict[str, Any]:
    return json.loads((output_folder / ".work" / "docker-compose.json").read_text(encoding="utf-8"))


def played(tmp_path: Path, match: Match, docker: FakeDocker | None = None) -> Path:
    """Plays `match` with a fake Docker, keeping the work folder, and returns the output folder."""
    output_folder = tmp_path / "out"
    engine = engine_with(docker or FakeDocker(controller_result=_RESULT_OF_A_WIN), keep_work_folder=True)
    engine.play(match, output_folder=output_folder)
    return output_folder


@pytest.fixture
def match(tmp_path: Path) -> Match:
    bots = tmp_path / "bots"
    return Match(
        player_1=Bot.from_folder(make_python_bot(bots, "basic_bot"), race=Race.TERRAN),
        player_2=Bot.from_folder(make_python_bot(bots, "loser_bot"), race=Race.RANDOM),
        map_file=make_map(tmp_path, "AcropolisAIE"),
        limits=GameLimits(max_game_steps=1000, max_step_time_ms=50, step_time_strikes=5, max_real_time_seconds=600),
    )


def test_the_result_is_the_match_controller_s(tmp_path: Path, match: Match) -> None:
    result = engine_with(FakeDocker(controller_result=_RESULT_OF_A_WIN)).play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_1_WIN
    assert result.game_steps == 4480
    assert result.average_step_times == (0.002, None)


def test_the_replay_and_the_logs_are_kept_and_the_rest_removed(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    result = engine_with(FakeDocker(controller_result=_RESULT_OF_A_WIN)).play(match, output_folder=output_folder)
    assert result.replay_file == output_folder / "basic_bot-vs-loser_bot.SC2Replay"
    assert sorted(path.name for path in output_folder.iterdir()) == ["basic_bot-vs-loser_bot.SC2Replay", "logs"]
    logs = result.logs_folder
    assert (logs / "player_1-basic_bot" / "stderr.log").read_text(encoding="utf-8") == "seat 1 played"
    assert (logs / "player_2-loser_bot" / "stderr.log").is_file()
    assert (logs / "game" / "client-1.log").is_file()
    assert (logs / "match_controller" / "match_controller.log").is_file()


def test_the_match_controller_is_told_the_match_and_its_limits(tmp_path: Path, match: Match) -> None:
    work_folder = played(tmp_path, match) / ".work"
    config = ControllerConfig.from_file(work_folder / "controller" / "match.json")
    assert config.map_path == "/root/StarCraftII/maps/AcropolisAIE.SC2Map"
    assert [(seat.name, seat.race, seat.bot_host) for seat in config.seats] == [
        ("basic_bot", "T", "bot_1"),
        ("loser_bot", "R", "bot_2"),
    ]
    assert (config.max_game_steps, config.max_step_time_ms, config.step_time_strikes) == (1000, 50, 5)
    assert (work_folder / "maps" / "AcropolisAIE.SC2Map").read_bytes() == b"a map"


def test_a_ladder_s_step_time_limit_stays_off_unless_set(tmp_path: Path, match: Match) -> None:
    unlimited = Match(player_1=match.player_1, player_2=match.player_2, map_file=match.map_file)
    work_folder = played(tmp_path, unlimited) / ".work"
    assert ControllerConfig.from_file(work_folder / "controller" / "match.json").max_step_time_ms is None


def test_the_match_controller_runs_from_copies_of_its_code_and_packages_alone(tmp_path: Path, match: Match) -> None:
    output_folder = played(tmp_path, match)
    work_folder = (output_folder / ".work").resolve()
    controller_folder = work_folder / "controller"
    for module in ["match_controller/__main__.py", "websockets/__init__.py", "s2clientprotocol/sc2api_pb2.py"]:
        assert (controller_folder / module).is_file()
    assert (controller_folder / "google" / "protobuf" / "__init__.py").is_file()
    assert not list(controller_folder.rglob("__pycache__"))
    controller = compose_project(output_folder)["services"]["match_controller"]
    assert controller["volumes"] == [
        f"{work_folder}/controller:/controller:ro",
        f"{work_folder}/output:/output",
        f"{work_folder}/logs/match_controller:/logs",
    ]
    assert controller["read_only"] is True
    assert controller["entrypoint"][-2:] == ["match_controller", "/controller/match.json"]


def test_each_bot_sees_its_own_copy_alone_and_may_only_read_it(tmp_path: Path, match: Match) -> None:
    output_folder = played(tmp_path, match)
    work_folder = (output_folder / ".work").resolve()
    services = compose_project(output_folder)["services"]
    assert services["bot_1"]["volumes"] == [f"{work_folder}/bots/1:/bots:ro", f"{work_folder}/logs/player_1:/logs"]
    assert services["bot_2"]["volumes"] == [f"{work_folder}/bots/2:/bots:ro", f"{work_folder}/logs/player_2:/logs"]
    assert services["bot_1"]["read_only"] is True and services["bot_1"]["working_dir"] == "/bots/basic_bot"
    assert (work_folder / "bots" / "1" / "basic_bot" / "data").is_dir()
    assert {name: service["image"] for name, service in services.items()} == {
        "game": "aiarena/arenaclient-sc2:v0.8.0",
        "bot_1": "aiarena/arenaclient-bot-base:v0.8.0",
        "bot_2": "aiarena/arenaclient-bot-base:v0.8.0",
        "match_controller": "aiarena/arenaclient-bot-base:v0.8.0",
    }


def test_each_bot_reaches_the_match_controller_alone_and_nothing_reaches_out(tmp_path: Path, match: Match) -> None:
    project = compose_project(played(tmp_path, match))
    networks = {name: service["networks"] for name, service in project["services"].items()}
    assert networks == {
        "game": ["game"],
        "bot_1": ["player_1"],
        "bot_2": ["player_2"],
        "match_controller": ["player_1", "player_2", "game"],
    }
    assert all(network["internal"] for network in project["networks"].values())


def test_every_container_runs_as_this_user_with_no_privileges(tmp_path: Path, match: Match) -> None:
    for service in compose_project(played(tmp_path, match))["services"].values():
        assert service["user"] == f"{os.getuid()}:{os.getgid()}"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["init"] is True


def test_under_rootless_docker_containers_run_as_their_root_which_is_this_user(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    docker = FakeDocker(controller_result=_RESULT_OF_A_WIN)
    ScalaEngine(run_command=docker, rootless=True, keep_work_folder=True).play(match, output_folder=output_folder)
    assert all("user" not in service for service in compose_project(output_folder)["services"].values())


def test_the_containers_take_the_match_s_resource_limits(tmp_path: Path, match: Match) -> None:
    limited = Match(
        player_1=match.player_1,
        player_2=match.player_2,
        map_file=match.map_file,
        resource_limits=ResourceLimits(bot_cpus=1.5, bot_memory_mib=1000, bot_processes=64, game_cpus=3),
    )
    services = compose_project(played(tmp_path, limited))["services"]
    bot = services["bot_2"]
    assert (bot["cpus"], bot["mem_limit"], bot["memswap_limit"], bot["pids_limit"]) == (1.5, "1000m", "1000m", 64)
    assert bot["tmpfs"] == ["/tmp:size=1000m,mode=1777", "/bots/loser_bot/data:size=1000m,mode=1777"]
    assert (services["game"]["cpus"], services["game"]["mem_limit"]) == (3, "6144m")
    controller = services["match_controller"]
    assert (controller["cpus"], controller["mem_limit"]) == (1.0, "1024m")


@pytest.mark.parametrize(
    ("entry_point", "command"),
    [
        ("run.py", "python run.py"),
        ("some_bot.dll", "dotnet some_bot.dll"),
        ("some_bot.jar", "java -jar some_bot.jar"),
        ("some_bot.js", "node some_bot.js"),
        ("some_bot", "./some_bot"),
    ],
)
def test_each_bot_starts_as_ai_arena_starts_its_type(
    tmp_path: Path, match: Match, entry_point: str, command: str
) -> None:
    folder = tmp_path / "bots" / "some_bot"
    folder.mkdir()
    (folder / entry_point).write_bytes(b"a bot")
    some_bot = Bot.from_folder(folder)
    output_folder = played(tmp_path, Match(player_1=some_bot, player_2=match.player_2, map_file=match.map_file))
    script = compose_project(output_folder)["services"]["bot_1"]["entrypoint"][-1].replace("$$", "$")
    assert (
        f'exec {command} --GamePort 8081 --LadderServer "$controller" --StartPort 8081 --OpponentId loser_bot '
        ">>/logs/stderr.log 2>&1"
    ) in script
    assert "getent hosts match_controller" in script


def test_a_linux_bot_s_binary_is_made_executable(tmp_path: Path, match: Match) -> None:
    folder = tmp_path / "bots" / "linux_bot"
    folder.mkdir()
    (folder / "linux_bot").write_bytes(b"\x7fELF")
    linux_bot = Bot.from_folder(folder)
    output_folder = played(tmp_path, Match(player_1=linux_bot, player_2=match.player_2, map_file=match.map_file))
    assert os.access(output_folder / ".work" / "bots" / "1" / "linux_bot" / "linux_bot", os.X_OK)


def test_a_windows_bot_is_refused(tmp_path: Path, match: Match) -> None:
    windows_bot = Bot(name="WinBot", race=Race.PROTOSS, bot_type=BotType.CPP_WIN32, folder=tmp_path)
    docker = FakeDocker(controller_result=_RESULT_OF_A_WIN)
    with pytest.raises(EngineError, match="Windows bot"):
        engine_with(docker).play(
            Match(player_1=match.player_1, player_2=windows_bot, map_file=match.map_file), output_folder=tmp_path / "o"
        )
    assert docker.commands == []


def test_a_bot_whose_logs_grow_past_their_limit_is_stopped_and_loses(tmp_path: Path, match: Match) -> None:
    flooding = Match(
        player_1=match.player_1,
        player_2=match.player_2,
        map_file=match.map_file,
        resource_limits=ResourceLimits(bot_log_mib=1),
    )
    docker = FakeDocker(player_1_log_bytes=2 * _MEBIBYTE)
    result = engine_with(docker).play(flooding, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_1_CRASH
    assert docker.subcommands() == ["create", "up", "wait", "kill", "logs", "down"]
    assert docker.commands[3][-1] == "bot_1"


def test_a_match_with_no_result_raises_but_keeps_its_logs(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    with pytest.raises(EngineError, match="no result"):
        engine_with(FakeDocker()).play(match, output_folder=output_folder)
    assert (output_folder / "logs" / "match_controller" / "match_controller.log").is_file()
    assert not (output_folder / ".work" / "bots").exists()


def test_an_unknown_outcome_raises(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(controller_result={"outcome": "Player3Win", "game_steps": 1})
    with pytest.raises(EngineError, match="unknown outcome"):
        engine_with(docker).play(match, output_folder=tmp_path / "out")


def test_a_bot_can_play_itself(tmp_path: Path, match: Match) -> None:
    mirror = Match(player_1=match.player_1, player_2=match.player_1, map_file=match.map_file)
    output_folder = played(tmp_path, mirror)
    assert (output_folder / ".work" / "bots" / "2" / "basic_bot" / "run.py").is_file()


def test_the_containers_are_created_played_and_removed(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(controller_result=_RESULT_OF_A_WIN)
    engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "up", "wait", "logs", "down"]
    assert docker.commands[2][-1] == "match_controller"


def test_only_the_images_docker_lacks_are_pulled(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def docker(args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
        commands.append(list(args))
        return 1 if args[:3] == ["docker", "image", "inspect"] and "bot-base" in args[3] else 0

    ScalaEngine(run_command=docker).pull_images(log_file=tmp_path / "pull.log")
    assert [command for command in commands if command[1] == "pull"] == [
        ["docker", "pull", "--quiet", "aiarena/arenaclient-bot-base:v0.8.0"]
    ]


def test_compose_takes_no_variable_of_the_containers_scripts_for_its_own(tmp_path: Path, match: Match) -> None:
    services = compose_project(played(tmp_path, match))["services"]
    for service in ("game", "bot_1", "bot_2"):
        script = services[service]["entrypoint"][-1]
        assert "$" in script and script.count("$") == 2 * script.count("$$")


def test_the_game_s_container_starts_a_client_for_each_seat(tmp_path: Path, match: Match) -> None:
    game = compose_project(played(tmp_path, match))["services"]["game"]
    script = game["entrypoint"][-1].replace("$$", "$")
    for port in (5001, 5002):
        assert f"-listen 0.0.0.0 -port {port}" in script
    assert subprocess.run(["bash", "-n", "-c", script], check=False).returncode == 0


def test_a_match_past_its_time_is_stopped_and_cleaned_up(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(wait_times_out=True)
    with pytest.raises(MatchTimeoutError):
        engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "up", "wait", "logs", "down"]
    assert not (tmp_path / "out" / ".work" / "bots").exists()


def test_stopping_the_matches_kills_their_containers_and_refuses_later_matches(tmp_path: Path, match: Match) -> None:
    class DockerPlayingUntilKilled(FakeDocker):
        def __init__(self) -> None:
            super().__init__(controller_result=_RESULT_OF_A_WIN)
            self.waiting = threading.Event()
            self.killed = threading.Event()

        def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
            if list(args[6:]) == ["kill"]:
                self.killed.set()
            if "wait" in args:
                self.waiting.set()
                assert self.killed.wait(timeout=10)
            return super().__call__(args, timeout=timeout, log_file=log_file)

    docker = DockerPlayingUntilKilled()
    engine = engine_with(docker)
    stopped: list[MatchStoppedError] = []

    def play() -> None:
        try:
            engine.play(match, output_folder=tmp_path / "out")
        except MatchStoppedError as error:
            stopped.append(error)

    playing = threading.Thread(target=play)
    playing.start()
    assert docker.waiting.wait(timeout=10)
    engine.stop_matches()
    playing.join(timeout=10)
    assert [str(error) for error in stopped] == ["The match was stopped while it was played"]
    with pytest.raises(MatchStoppedError, match="before it began"):
        engine.play(match, output_folder=tmp_path / "later")


def test_the_containers_a_killed_match_left_are_removed_by_the_project_it_named(tmp_path: Path) -> None:
    docker = FakeDocker()
    project_file = tmp_path / "out" / ".work" / "compose-project"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("sc2scala-0123456789ab\n", encoding="utf-8")
    engine_with(docker).remove_leftovers(tmp_path / "out")
    assert docker.commands == [
        ["docker", "compose", "--project-name", "sc2scala-0123456789ab", "down", "--remove-orphans", "--timeout", "10"]
    ]
    assert not project_file.exists()


def test_containers_that_were_not_created_are_cleaned_up(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(create_exit_code=1)
    with pytest.raises(EngineError, match="could not create"):
        engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "logs", "down"]


def test_a_slow_compose_logs_still_removes_the_containers(tmp_path: Path, match: Match) -> None:
    class SlowLogsDocker(FakeDocker):
        def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
            if "logs" in args:
                self.commands.append(list(args))
                raise subprocess.TimeoutExpired(list(args), timeout or 0)
            return super().__call__(args, timeout=timeout, log_file=log_file)

    docker = SlowLogsDocker(controller_result=_RESULT_OF_A_WIN)
    result = engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_1_WIN
    assert docker.subcommands()[-1] == "down"
