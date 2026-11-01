from pathlib import Path

import pytest

from sc2scala import Bot, Match, Race, ResultCode, ScalaEngine
from sc2scala.computer import Computer, Difficulty
from sc2scala.engine_choice import ENGINE_NAMES, engine_named
from tests.bots import assemble_bot

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("engine", ENGINE_NAMES)
def test_a_bot_that_leaves_loses(tmp_path: Path, map_file: Path, engine: str) -> None:
    leaver = Bot.from_folder(assemble_bot("leaver_bot", tmp_path / "bots"), race=Race.TERRAN)
    marines = Bot.from_folder(assemble_bot("marine_bot", tmp_path / "bots"), race=Race.TERRAN)
    match = Match(player_1=leaver, player_2=marines, map_file=map_file)
    result = engine_named(engine).play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_2_WIN
    assert result.replay_file is not None
    assert result.replay_file.stat().st_size > 0
    assert (result.logs_folder / "player_2-marine_bot").is_dir()
    assert [path for path in tmp_path.rglob("*") if path.owner() != Path.home().owner()] == []


def test_a_bot_that_plays_itself_plays_both_players_on_scala_s_engine(tmp_path: Path, map_file: Path) -> None:
    marines = Bot.from_folder(assemble_bot("marine_bot", tmp_path / "bots"), race=Race.TERRAN)
    result = ScalaEngine().play(
        Match(player_1=marines, player_2=marines, map_file=map_file), output_folder=tmp_path / "o"
    )
    assert result.code in (ResultCode.PLAYER_1_WIN, ResultCode.PLAYER_2_WIN)
    logs = [(result.logs_folder / f"player_{seat}-marine_bot" / "stderr.log").read_text() for seat in (1, 2)]
    assert "Player 1 - Bot" in logs[0] and "Player 2 - Bot" in logs[1]


def test_a_bot_that_leaves_loses_to_the_computer_on_scala_s_engine(tmp_path: Path, map_file: Path) -> None:
    leaver = Bot.from_folder(assemble_bot("leaver_bot", tmp_path / "bots"), race=Race.TERRAN)
    computer = Computer(Race.RANDOM, Difficulty.VERY_EASY)
    result = ScalaEngine().play(
        Match(player_1=leaver, player_2=computer, map_file=map_file), output_folder=tmp_path / "o"
    )
    assert result.code == ResultCode.PLAYER_2_WIN
    assert result.replay_file is not None and result.replay_file.stat().st_size > 0
    assert result.average_step_times[1] is None
    assert [path.name for path in result.logs_folder.glob("player_*")] == ["player_1-leaver_bot"]
