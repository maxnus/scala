"""Bots for the tests that play a match, on python-sc2 as AI Arena's bot image holds it."""

import shutil
from pathlib import Path

_BOTS_FOLDER = Path(__file__).parent
TEST_BOTS = ("marine_bot", "leaver_bot")


def assemble_bot(bot: str, parent: Path, *, name: str | None = None) -> Path:
    """A Python bot's folder in `parent`, named `name` or else `bot`, that plays as the module `bot` of this folder.

    Raises `ValueError` for a `bot` not in `TEST_BOTS`.
    """
    if bot not in TEST_BOTS:
        raise ValueError(f"{bot!r} is none of the test bots: {', '.join(TEST_BOTS)}")
    folder = parent / (name or bot)
    folder.mkdir(parents=True)
    shutil.copyfile(_BOTS_FOLDER / "run.py", folder / "run.py")
    shutil.copyfile(_BOTS_FOLDER / "ladder_join.py", folder / "ladder_join.py")
    shutil.copyfile(_BOTS_FOLDER / f"{bot}.py", folder / "bot.py")
    return folder
