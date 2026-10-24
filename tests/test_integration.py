from pathlib import Path

import pytest

from sc2scala import AiArenaEngine, Bot, Match, Race, ResultCode
from tests.bots import assemble_bot

pytestmark = pytest.mark.integration


def test_a_bot_that_leaves_loses(tmp_path: Path, map_file: Path) -> None:
    leaver = Bot.from_folder(assemble_bot("leaver_bot", tmp_path / "bots"), race=Race.TERRAN)
    marines = Bot.from_folder(assemble_bot("marine_bot", tmp_path / "bots"), race=Race.TERRAN)
    match = Match(player_1=leaver, player_2=marines, map_file=map_file)
    result = AiArenaEngine().play(match, output_folder=tmp_path / "out")
    assert result.code == ResultCode.PLAYER_2_WIN
    assert result.replay_file is not None
    assert result.replay_file.stat().st_size > 0
    assert (result.logs_folder / "player_2-marine_bot").is_dir()
    assert [path for path in tmp_path.rglob("*") if path.owner() != Path.home().owner()] == []
