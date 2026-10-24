import json
from datetime import datetime
from pathlib import Path

import pytest

from sc2scala import EngineError, Match, MatchResult, Race, ResultCode
from sc2scala.cli import main
from tests.bot_folders import make_map, make_python_bot


class RecordingEngine:
    """Stands in for the engine: records the match it is given, and returns `result` or raises `error`."""

    def __init__(self, *, error: EngineError | None = None) -> None:
        self.error = error
        self.matches: list[Match] = []

    def stop_matches(self) -> None:
        pass

    def remove_leftovers(self, output_folder: Path) -> None:
        pass

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        self.matches.append(match)
        if self.error is not None:
            raise self.error
        output_folder.mkdir(parents=True, exist_ok=True)
        return MatchResult(
            code=ResultCode.PLAYER_2_WIN,
            game_steps=2240,
            average_step_times=(0.001, 0.004),
            real_time_seconds=65.0,
            replay_file=output_folder / "1_basic_bot_vs_loser_bot.SC2Replay",
            logs_folder=output_folder / "logs",
        )


@pytest.fixture
def play_arguments(tmp_path: Path) -> list[str]:
    bot_1 = make_python_bot(tmp_path / "bots", "basic_bot")
    bot_2 = make_python_bot(tmp_path / "bots", "loser_bot")
    return ["play", str(bot_1), str(bot_2), "--map", str(make_map(tmp_path, "AcropolisAIE"))]


def test_play_writes_the_result(tmp_path: Path, play_arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    output_folder = tmp_path / "out"
    assert main([*play_arguments, "--output", str(output_folder)], engine=RecordingEngine()) == 0
    result = json.loads((output_folder / "result.json").read_text(encoding="utf-8"))
    assert result == {
        "result": "Player2Win",
        "map": "AcropolisAIE",
        "game_steps": 2240,
        "game_seconds": 100.0,
        "real_time_seconds": 65.0,
        "players": [
            {"name": "basic_bot", "race": "R", "type": "python", "average_step_time": 0.001},
            {"name": "loser_bot", "race": "R", "type": "python", "average_step_time": 0.004},
        ],
        "replay": "1_basic_bot_vs_loser_bot.SC2Replay",
    }
    assert "Player2Win after 1:40 of game time, 1:05 of real time" in capsys.readouterr().out


def test_races_are_given_by_letter_or_name(tmp_path: Path, play_arguments: list[str]) -> None:
    engine = RecordingEngine()
    main([*play_arguments, "--races", "t", "Protoss", "--output", str(tmp_path / "out")], engine=engine)
    assert (engine.matches[0].player_1.race, engine.matches[0].player_2.race) == (Race.TERRAN, Race.PROTOSS)


def test_a_bot_folder_that_is_not_there_is_refused(tmp_path: Path, play_arguments: list[str]) -> None:
    play_arguments[1] = str(tmp_path / "missing_bot")
    with pytest.raises(SystemExit) as exit_info:
        main(play_arguments, engine=RecordingEngine())
    assert exit_info.value.code == 2


def test_an_engine_that_cannot_play_fails_the_command(tmp_path: Path, play_arguments: list[str]) -> None:
    engine = RecordingEngine(error=EngineError("Docker is not installed"))
    assert main([*play_arguments, "--output", str(tmp_path / "out")], engine=engine) == 1


def test_an_output_folder_that_holds_anything_is_refused(tmp_path: Path, play_arguments: list[str]) -> None:
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    (output_folder / "result.json").write_text("{}", encoding="utf-8")
    engine = RecordingEngine()
    with pytest.raises(SystemExit) as exit_info:
        main([*play_arguments, "--output", str(output_folder)], engine=engine)
    assert exit_info.value.code == 2
    assert engine.matches == []


def test_runs_started_in_the_same_second_write_to_folders_of_their_own(
    tmp_path: Path, play_arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class ClockStoppedAtNoon:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 10, 24, 12, 0, 0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sc2scala.play_command.datetime", ClockStoppedAtNoon)
    assert main(play_arguments, engine=RecordingEngine()) == 0
    assert main(play_arguments, engine=RecordingEngine()) == 0
    assert sorted(folder.name for folder in (tmp_path / "matches").iterdir()) == [
        "20261024-120000-basic_bot-vs-loser_bot",
        "20261024-120000-basic_bot-vs-loser_bot-2",
    ]
