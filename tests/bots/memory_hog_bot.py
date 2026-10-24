"""A bot that takes another 256 MiB of memory on every step, until it is stopped."""

from sc2.bot_ai import BotAI
from sc2.data import Race
from sc2.player import Bot

_BYTES_PER_STEP = 256 * 1024 * 1024


class MemoryHogBot(BotAI):
    def __init__(self) -> None:
        super().__init__()
        self.hoard: list[bytearray] = []

    async def on_step(self, iteration: int) -> None:
        self.hoard.append(bytearray(b"x" * _BYTES_PER_STEP))


BOT = Bot(Race.Terran, MemoryHogBot())
