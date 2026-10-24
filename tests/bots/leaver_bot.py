"""A Terran bot that leaves the game on its first step, which concedes it."""

from sc2.bot_ai import BotAI
from sc2.data import Race
from sc2.player import Bot


class LeaverBot(BotAI):
    async def on_step(self, iteration: int) -> None:
        await self.client.leave()


BOT = Bot(Race.Terran, LeaverBot())
