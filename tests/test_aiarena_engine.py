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
    AiArenaEngine,
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
)
from tests.bot_folders import make_map, make_python_bot

_RESULT_OF_A_WIN = {"match": 1, "type": "Player1Win", "game_steps": 4480, "bot1_avg_step_time": 0.002}
_MEBIBYTE = 1024 * 1024


class FakeDocker:
    """Stands in for `docker`, recording each command and writing, while `wait` waits, what the engine writes.

    With `player_1_log_bytes`, player 1's log grows to that size during `wait`, which then waits for player 1 to be
    killed.
    """

    def __init__(
        self,
        *,
        engine_result: dict[str, Any] | None = None,
        create_exit_code: int = 0,
        wait_times_out: bool = False,
        player_1_log_bytes: int = 0,
    ) -> None:
        self.engine_result = engine_result
        self.create_exit_code = create_exit_code
        self.wait_times_out = wait_times_out
        self.player_1_log_bytes = player_1_log_bytes
        self.commands: list[list[str]] = []

    def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
        self.commands.append(list(args))
        if "create" in args:
            return self.create_exit_code
        if "wait" in args:
            if self.wait_times_out:
                raise subprocess.TimeoutExpired(list(args), timeout or 0)
            self._write_what_the_engine_writes(Path(args[args.index("--file") + 1]).parent)
        return 0

    def subcommands(self) -> list[str]:
        """Each Docker Compose command's subcommand: `create`, `up`, `wait`, `kill`, `logs` or `down`."""
        return [command[6] for command in self.commands]

    def _write_what_the_engine_writes(self, work_folder: Path) -> None:
        for bot_folder in (work_folder / "bots").glob("*/*"):
            bot_logs = work_folder / "logs" / f"bot_controller{bot_folder.parent.name}" / bot_folder.name
            bot_logs.mkdir()
            (bot_logs / "stderr.log").write_text(f"{bot_folder.name} played", encoding="utf-8")
        if self.player_1_log_bytes:
            flood = next((work_folder / "logs" / "bot_controller1").iterdir()) / "flood.log"
            flood.write_bytes(b"x" * self.player_1_log_bytes)
            self._wait_for_kill()
            return
        if self.engine_result is not None:
            results = {"results": [self.engine_result]}
            (work_folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (work_folder / "replays" / "1_basic_bot_vs_loser_bot.SC2Replay").write_bytes(b"a replay")
        (work_folder / "logs" / "proxy_controller").mkdir()
        (work_folder / "logs" / "proxy_controller" / "proxy_controller.log").write_text("ok", encoding="utf-8")

    def _wait_for_kill(self) -> None:
        deadline = time.monotonic() + 10
        while ["kill", "bot_controller1"] not in [
            command[6:] for command in self.commands
        ] and time.monotonic() < deadline:
            time.sleep(0.05)


def engine_with(docker: FakeDocker, **options: Any) -> AiArenaEngine:
    return AiArenaEngine(run_command=docker, rootless=False, **options)


def compose_services(output_folder: Path) -> dict[str, Any]:
    compose_file = output_folder / ".work" / "docker-compose.json"
    return json.loads(compose_file.read_text(encoding="utf-8"))


@pytest.fixture
def match(tmp_path: Path) -> Match:
    bots = tmp_path / "bots"
    return Match(
        player_1=Bot.from_folder(make_python_bot(bots, "basic_bot"), race=Race.TERRAN),
        player_2=Bot.from_folder(make_python_bot(bots, "loser_bot"), race=Race.ZERG),
        map_file=make_map(tmp_path, "AcropolisAIE"),
        limits=GameLimits(max_game_steps=1000, max_step_time_ms=50, step_time_strikes=5, max_real_time_seconds=600),
    )


def test_the_result_is_the_engines(tmp_path: Path, match: Match) -> None:
    result = engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_1_WIN
    assert result.game_steps == 4480
    assert result.game_seconds == pytest.approx(200)
    assert result.average_step_times == (0.002, None)


def test_the_replay_and_the_logs_are_kept_and_the_rest_removed(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    result = engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(match, output_folder=output_folder)
    assert result.replay_file == output_folder / "1_basic_bot_vs_loser_bot.SC2Replay"
    assert result.logs_folder == output_folder / "logs"
    assert sorted(path.name for path in output_folder.iterdir()) == ["1_basic_bot_vs_loser_bot.SC2Replay", "logs"]
    assert (result.logs_folder / "player_1-basic_bot" / "stderr.log").read_text(encoding="utf-8") == "basic_bot played"
    assert (result.logs_folder / "player_2-loser_bot" / "stderr.log").is_file()
    assert (result.logs_folder / "proxy_controller" / "proxy_controller.log").is_file()


def test_the_engine_reads_the_match_and_its_limits(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        match, output_folder=output_folder
    )
    work_folder = output_folder / ".work"
    assert (work_folder / "matches").read_text(encoding="utf-8") == (
        "basic_bot,basic_bot,T,python,loser_bot,loser_bot,Z,python,AcropolisAIE\n"
    )
    config = (work_folder / "config.toml").read_text(encoding="utf-8")
    for setting in ["MAX_GAME_TIME = 1000", "MAX_FRAME_TIME = 50", "STRIKES = 5", "MAX_REAL_TIME = 600"]:
        assert setting in config
    assert (work_folder / "maps" / "AcropolisAIE.SC2Map").read_bytes() == b"a map"
    assert (work_folder / "bots" / "1" / "basic_bot" / "run.py").is_file()


def test_each_bot_controller_sees_its_own_bot_alone_and_may_only_read_it(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        match, output_folder=output_folder
    )
    work_folder = (output_folder / ".work").resolve()
    services = compose_services(output_folder)["services"]
    assert f"{work_folder}/bots/1:/bots:ro" in services["bot_controller1"]["volumes"]
    assert f"{work_folder}/bots/2:/bots:ro" in services["bot_controller2"]["volumes"]
    assert not any("/bots" in volume for volume in services["proxy_controller"]["volumes"])
    assert services["bot_controller1"]["read_only"] is True
    assert (work_folder / "bots" / "1" / "basic_bot" / "data").is_dir()
    assert {service["image"] for service in services.values()} == {
        "aiarena/arenaclient-sc2:v0.8.0",
        "aiarena/arenaclient-bot:v0.8.0",
        "aiarena/arenaclient-proxy:v0.8.0",
    }


def test_each_bot_reaches_the_match_controller_alone_and_nothing_reaches_out(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        match, output_folder=output_folder
    )
    project = compose_services(output_folder)
    networks = {name: service["networks"] for name, service in project["services"].items()}
    assert networks == {
        "sc2_controller": ["game"],
        "bot_controller1": ["player_1"],
        "bot_controller2": ["player_2"],
        "proxy_controller": ["player_1", "player_2", "game"],
    }
    assert all(network["internal"] for network in project["networks"].values())


def test_every_container_runs_as_this_user_with_no_privileges(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        match, output_folder=output_folder
    )
    for service in compose_services(output_folder)["services"].values():
        assert service["user"] == f"{os.getuid()}:{os.getgid()}"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]


def test_under_rootless_docker_containers_run_as_their_root_which_is_this_user(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine = AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN), rootless=True, keep_work_folder=True)
    engine.play(match, output_folder=output_folder)
    assert all("user" not in service for service in compose_services(output_folder)["services"].values())


def test_the_containers_take_the_matchs_resource_limits(tmp_path: Path, match: Match) -> None:
    limited = Match(
        player_1=match.player_1,
        player_2=match.player_2,
        map_file=match.map_file,
        resource_limits=ResourceLimits(bot_cpus=1.5, bot_memory_mib=1000, bot_processes=64, game_cpus=3),
    )
    output_folder = tmp_path / "out"
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        limited, output_folder=output_folder
    )
    services = compose_services(output_folder)["services"]
    bot = services["bot_controller2"]
    assert (bot["cpus"], bot["mem_limit"], bot["memswap_limit"], bot["pids_limit"]) == (1.5, "1000m", "1000m", 64)
    assert bot["tmpfs"] == ["/tmp:size=1000m,mode=1777", "/bots/loser_bot/data:size=1000m,mode=1777"]
    game = services["sc2_controller"]
    assert (game["cpus"], game["mem_limit"]) == (3, "6144m")
    match_controller = services["proxy_controller"]
    assert (match_controller["cpus"], match_controller["mem_limit"]) == (1.0, "1024m")


def test_a_linux_bots_binary_is_made_executable(tmp_path: Path, match: Match) -> None:
    folder = tmp_path / "bots" / "linux_bot"
    folder.mkdir()
    (folder / "linux_bot").write_bytes(b"\x7fELF")
    linux_bot = Bot.from_folder(folder)
    output_folder = tmp_path / "out"
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        Match(player_1=linux_bot, player_2=match.player_2, map_file=match.map_file), output_folder=output_folder
    )
    assert os.access(output_folder / ".work" / "bots" / "1" / "linux_bot" / "linux_bot", os.X_OK)


def test_a_windows_bot_is_refused(tmp_path: Path, match: Match) -> None:
    windows_bot = Bot(name="WinBot", race=Race.PROTOSS, bot_type=BotType.CPP_WIN32, folder=tmp_path)
    docker = FakeDocker(engine_result=_RESULT_OF_A_WIN)
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
    assert docker.commands[3][-1] == "bot_controller1"
    assert (result.logs_folder / "player_1-basic_bot" / "flood.log").stat().st_size < 2 * _MEBIBYTE


def test_a_bot_can_play_itself(tmp_path: Path, match: Match) -> None:
    mirror = Match(player_1=match.player_1, player_2=match.player_1, map_file=match.map_file)
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True).play(
        mirror, output_folder=tmp_path / "out"
    )
    for seat in ("1", "2"):
        assert (tmp_path / "out" / ".work" / "bots" / seat / "basic_bot" / "run.py").is_file()


def test_the_containers_are_created_played_and_removed(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(engine_result=_RESULT_OF_A_WIN)
    engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "up", "wait", "logs", "down"]
    assert docker.commands[2][-1] == "proxy_controller"


def test_a_match_past_its_time_is_stopped_and_cleaned_up(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(wait_times_out=True)
    with pytest.raises(MatchTimeoutError):
        engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "up", "wait", "logs", "down"]


class DockerPlayingUntilKilled(FakeDocker):
    """Stands in for `docker`, its `wait` waiting until the match's containers are killed."""

    def __init__(self) -> None:
        super().__init__(engine_result=_RESULT_OF_A_WIN)
        self.waiting = threading.Event()
        self.killed = threading.Event()

    def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
        if list(args[6:]) == ["kill"]:
            self.killed.set()
        if "wait" in args:
            self.waiting.set()
            assert self.killed.wait(timeout=10)
        return super().__call__(args, timeout=timeout, log_file=log_file)


def test_stopping_the_matches_kills_their_containers_and_refuses_later_matches(tmp_path: Path, match: Match) -> None:
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
    assert docker.subcommands() == ["create", "up", "kill", "wait", "logs", "down"]
    with pytest.raises(MatchStoppedError, match="before it began"):
        engine.play(match, output_folder=tmp_path / "later")
    assert len(docker.commands) == 6


class DockerNamingItsProject(FakeDocker):
    """Stands in for `docker`, recording what the work folder names at `create`."""

    def __init__(self) -> None:
        super().__init__(engine_result=_RESULT_OF_A_WIN)
        self.named_at_create: str | None = None

    def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
        if "create" in args:
            project_file = Path(args[args.index("--file") + 1]).parent / "compose-project"
            self.named_at_create = project_file.read_text(encoding="utf-8").strip()
        return super().__call__(args, timeout=timeout, log_file=log_file)


def test_a_match_names_its_project_while_its_containers_may_exist(tmp_path: Path, match: Match) -> None:
    docker = DockerNamingItsProject()
    output_folder = tmp_path / "out"
    engine_with(docker, keep_work_folder=True).play(match, output_folder=output_folder)
    assert docker.named_at_create == docker.commands[0][3]
    assert not (output_folder / ".work" / "compose-project").exists()


def test_the_containers_a_killed_match_left_are_removed_by_the_project_it_named(tmp_path: Path) -> None:
    docker = FakeDocker()
    project_file = tmp_path / "out" / ".work" / "compose-project"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("sc2scala-0123456789ab\n", encoding="utf-8")
    engine = engine_with(docker)
    engine.remove_leftovers(tmp_path / "out")
    assert docker.commands == [
        ["docker", "compose", "--project-name", "sc2scala-0123456789ab", "down", "--remove-orphans", "--timeout", "10"]
    ]
    assert not project_file.exists()
    engine.remove_leftovers(tmp_path / "out")
    assert len(docker.commands) == 1


def test_containers_that_were_not_created_are_cleaned_up(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(create_exit_code=1)
    with pytest.raises(EngineError, match="could not create"):
        engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "logs", "down"]


def test_a_match_with_no_result_raises_but_keeps_its_logs(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    with pytest.raises(EngineError, match="no result"):
        engine_with(FakeDocker(engine_result=None)).play(match, output_folder=output_folder)
    assert (output_folder / "logs" / "player_1-basic_bot" / "stderr.log").is_file()


def test_a_result_of_unknown_type_raises(tmp_path: Path, match: Match) -> None:
    engine = engine_with(FakeDocker(engine_result={"match": 1, "type": "Placeholder", "game_steps": 0}))
    with pytest.raises(EngineError, match="unknown type"):
        engine.play(match, output_folder=tmp_path / "out")


def test_an_output_folder_must_be_empty(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    (output_folder / "something").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(match, output_folder=output_folder)


def test_a_work_folder_that_cannot_be_removed_leaves_the_result(
    tmp_path: Path, match: Match, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_to_remove(path: Path) -> None:
        raise PermissionError(f"{path} belongs to root")

    monkeypatch.setattr("sc2scala.aiarena_engine.shutil.rmtree", refuse_to_remove)
    result = engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_1_WIN


def test_a_linux_bots_binary_that_is_a_link_is_left_as_it_is(tmp_path: Path, match: Match) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"not the bot's")
    outside.chmod(0o644)
    folder = tmp_path / "bots" / "linked_bot"
    folder.mkdir()
    (folder / "linked_bot").symlink_to(outside)
    engine_with(FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(
        Match(player_1=Bot.from_folder(folder), player_2=match.player_2, map_file=match.map_file),
        output_folder=tmp_path / "out",
    )
    assert outside.stat().st_mode & 0o777 == 0o644


def test_a_slow_compose_logs_still_removes_the_containers(tmp_path: Path, match: Match) -> None:
    class SlowLogsDocker(FakeDocker):
        def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
            if "logs" in args:
                self.commands.append(list(args))
                raise subprocess.TimeoutExpired(list(args), timeout or 0)
            return super().__call__(args, timeout=timeout, log_file=log_file)

    docker = SlowLogsDocker(engine_result=_RESULT_OF_A_WIN)
    result = engine_with(docker).play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_1_WIN
    assert docker.subcommands()[-1] == "down"


def test_only_the_images_docker_lacks_are_pulled(tmp_path: Path) -> None:
    class DockerLackingTheGame(FakeDocker):
        def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
            self.commands.append(list(args))
            return 1 if args[1:3] == ["image", "inspect"] and "arenaclient-sc2" in args[3] else 0

    docker = DockerLackingTheGame()
    engine_with(docker).pull_images(log_file=tmp_path / "pull.log")
    assert [command[3] for command in docker.commands if command[1] == "pull"] == ["aiarena/arenaclient-sc2:v0.8.0"]
