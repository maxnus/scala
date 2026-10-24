"""A bot that writes 4 MiB to its log on every step, until it is stopped."""

import sys

from sc2.bot_ai import BotAI
from sc2.data import Race
from sc2.player import Bot

_LINE = "flood " * 170 + "\n"
_LINES_PER_STEP = 4 * 1024 * 1024 // len(_LINE)


class LogFloodBot(BotAI):
    async def on_step(self, iteration: int) -> None:
        sys.stdout.write(_LINE * _LINES_PER_STEP)
        sys.stdout.flush()


BOT = Bot(Race.Terran, LogFloodBot())
