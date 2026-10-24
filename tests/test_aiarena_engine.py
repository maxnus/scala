import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sc2scala import AiArenaEngine, Bot, EngineError, GameLimits, Match, MatchTimeoutError, Race, ResultCode
from tests.bot_folders import make_map, make_python_bot

_RESULT_OF_A_WIN = {"match": 1, "type": "Player1Win", "game_steps": 4480, "bot1_avg_step_time": 0.002}


class FakeDocker:
    """Stands in for `docker`, recording each command and writing, on `up`, what the engine writes after a match."""

    def __init__(
        self,
        *,
        engine_result: dict[str, Any] | None = None,
        create_exit_code: int = 0,
        up_times_out: bool = False,
    ) -> None:
        self.engine_result = engine_result
        self.create_exit_code = create_exit_code
        self.up_times_out = up_times_out
        self.commands: list[list[str]] = []

    def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
        self.commands.append(list(args))
        if "create" in args:
            return self.create_exit_code
        if "up" in args:
            if self.up_times_out:
                raise subprocess.TimeoutExpired(list(args), timeout or 0)
            self._write_what_the_engine_writes(Path(args[args.index("--file") + 1]).parent)
        return 0

    def subcommands(self) -> list[str]:
        """Each command's subcommand: `create`, `up` and `down` for Docker Compose, `run` for Docker itself."""
        return [command[6] if command[1] == "compose" else command[1] for command in self.commands]

    def _write_what_the_engine_writes(self, work_folder: Path) -> None:
        if self.engine_result is not None:
            results = {"results": [self.engine_result]}
            (work_folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (work_folder / "replays" / "1_basic_bot_vs_loser_bot.SC2Replay").write_bytes(b"a replay")
        (work_folder / "logs" / "proxy_controller").mkdir()
        (work_folder / "logs" / "proxy_controller" / "proxy_controller.log").write_text("ok", encoding="utf-8")
        for bot_folder in (work_folder / "bots").glob("*/*"):
            bot_logs = work_folder / "logs" / f"bot_controller{bot_folder.parent.name}" / bot_folder.name
            bot_logs.mkdir()
            (bot_logs / "stderr.log").write_text(f"{bot_folder.name} played", encoding="utf-8")


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
    result = AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(
        match, output_folder=tmp_path / "out"
    )
    assert result.code == ResultCode.PLAYER_1_WIN
    assert result.game_steps == 4480
    assert result.game_seconds == pytest.approx(200)
    assert result.average_step_times == (0.002, None)


def test_the_replay_and_the_logs_are_kept_and_the_rest_removed(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    result = AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(
        match, output_folder=output_folder
    )
    assert result.replay_file == output_folder / "1_basic_bot_vs_loser_bot.SC2Replay"
    assert result.logs_folder == output_folder / "logs"
    assert sorted(path.name for path in output_folder.iterdir()) == ["1_basic_bot_vs_loser_bot.SC2Replay", "logs"]
    assert (result.logs_folder / "player_1-basic_bot" / "stderr.log").read_text(encoding="utf-8") == "basic_bot played"
    assert (result.logs_folder / "player_2-loser_bot" / "stderr.log").is_file()
    assert (result.logs_folder / "proxy_controller" / "proxy_controller.log").is_file()


def test_the_engine_reads_the_match_and_its_limits(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine = AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True)
    engine.play(match, output_folder=output_folder)
    work_folder = output_folder / ".work"
    assert (work_folder / "matches").read_text(encoding="utf-8") == (
        "basic_bot,basic_bot,T,python,loser_bot,loser_bot,Z,python,AcropolisAIE\n"
    )
    config = (work_folder / "config.toml").read_text(encoding="utf-8")
    for setting in ["MAX_GAME_TIME = 1000", "MAX_FRAME_TIME = 50", "STRIKES = 5", "MAX_REAL_TIME = 600"]:
        assert setting in config
    assert (work_folder / "maps" / "AcropolisAIE.SC2Map").read_bytes() == b"a map"
    assert (work_folder / "bots" / "1" / "basic_bot" / "run.py").is_file()


def test_each_bot_controller_sees_its_own_bot_alone(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    engine = AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True)
    engine.play(match, output_folder=output_folder)
    work_folder = (output_folder / ".work").resolve()
    services = json.loads((work_folder / "docker-compose.json").read_text(encoding="utf-8"))["services"]
    assert f"{work_folder}/bots/1:/bots" in services["bot_controller1"]["volumes"]
    assert f"{work_folder}/bots/2:/bots" in services["bot_controller2"]["volumes"]
    assert not any("/bots" in volume for volume in services["proxy_controller"]["volumes"])
    assert {service["image"] for service in services.values()} == {
        "aiarena/arenaclient-sc2:v0.8.0",
        "aiarena/arenaclient-bot:v0.8.0",
        "aiarena/arenaclient-proxy:v0.8.0",
    }


def test_a_bot_can_play_itself(tmp_path: Path, match: Match) -> None:
    mirror = Match(player_1=match.player_1, player_2=match.player_1, map_file=match.map_file)
    engine = AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN), keep_work_folder=True)
    engine.play(mirror, output_folder=tmp_path / "out")
    for seat in ("1", "2"):
        assert (tmp_path / "out" / ".work" / "bots" / seat / "basic_bot" / "run.py").is_file()


def test_the_containers_are_created_played_removed_and_their_files_handed_back(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(engine_result=_RESULT_OF_A_WIN)
    AiArenaEngine(run_command=docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "up", "down", "run"]
    assert docker.commands[1][-2:] == ["--exit-code-from", "proxy_controller"]


def test_a_match_past_its_time_is_stopped_and_cleaned_up(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(up_times_out=True)
    with pytest.raises(MatchTimeoutError):
        AiArenaEngine(run_command=docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "up", "down", "run"]


def test_containers_that_were_not_created_are_cleaned_up(tmp_path: Path, match: Match) -> None:
    docker = FakeDocker(create_exit_code=1)
    with pytest.raises(EngineError, match="could not create"):
        AiArenaEngine(run_command=docker).play(match, output_folder=tmp_path / "out")
    assert docker.subcommands() == ["create", "down", "run"]


def test_a_match_with_no_result_raises_but_keeps_its_logs(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    with pytest.raises(EngineError, match="no result"):
        AiArenaEngine(run_command=FakeDocker(engine_result=None)).play(match, output_folder=output_folder)
    assert (output_folder / "logs" / "player_1-basic_bot" / "stderr.log").is_file()


def test_a_result_of_unknown_type_raises(tmp_path: Path, match: Match) -> None:
    engine = AiArenaEngine(run_command=FakeDocker(engine_result={"match": 1, "type": "Placeholder", "game_steps": 0}))
    with pytest.raises(EngineError, match="unknown type"):
        engine.play(match, output_folder=tmp_path / "out")


def test_an_output_folder_must_be_empty(tmp_path: Path, match: Match) -> None:
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    (output_folder / "something").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        AiArenaEngine(run_command=FakeDocker(engine_result=_RESULT_OF_A_WIN)).play(match, output_folder=output_folder)
