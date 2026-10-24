from pathlib import Path

import pytest

from sc2scala import Bot, BotType, Race
from tests.bot_folders import make_python_bot


def test_a_bot_is_named_for_its_folder(tmp_path: Path) -> None:
    bot = Bot.from_folder(make_python_bot(tmp_path, "basic_bot"), race=Race.ZERG)
    assert (bot.name, bot.race, bot.bot_type) == ("basic_bot", Race.ZERG, BotType.PYTHON)


def test_a_bot_plays_random_unless_told(tmp_path: Path) -> None:
    assert Bot.from_folder(make_python_bot(tmp_path, "basic_bot")).race == Race.RANDOM


@pytest.mark.parametrize("name", ["my bot", "bot,two", "", "bötchen", ".", "..", ".hidden", "-bot"])
def test_a_name_the_engine_cannot_take_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="letters, digits"):
        Bot(name=name, race=Race.TERRAN, bot_type=BotType.PYTHON, folder=tmp_path)


@pytest.mark.parametrize("name", ["bot.v2", "_bot", "9bot", "my-bot"])
def test_a_name_may_hold_dots_and_dashes_after_its_first_character(tmp_path: Path, name: str) -> None:
    assert Bot(name=name, race=Race.TERRAN, bot_type=BotType.PYTHON, folder=tmp_path).name == name
