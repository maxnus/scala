from pathlib import Path

import pytest

from sc2scala import Bot, Match
from tests.bot_folders import make_map, make_python_bot


def test_a_match_names_its_map_by_the_file(tmp_path: Path) -> None:
    bot = Bot.from_folder(make_python_bot(tmp_path, "basic_bot"))
    match = Match(player_1=bot, player_2=bot, map_file=make_map(tmp_path, "AcropolisAIE"))
    assert match.map_name == "AcropolisAIE"


def test_a_map_is_an_sc2map_file(tmp_path: Path) -> None:
    bot = Bot.from_folder(make_python_bot(tmp_path, "basic_bot"))
    with pytest.raises(ValueError, match=r"\.SC2Map"):
        Match(player_1=bot, player_2=bot, map_file=tmp_path / "AcropolisAIE.zip")
