"""A Terran bot that trains marines from three barracks and attacks with sixteen or more.

With no enemy structure in sight, the marines search the expansions in turn, so that a game ends before its time limit.
"""

from sc2.bot_ai import BotAI
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.player import Bot

_MAX_WORKERS = 22
_BARRACKS_WANTED = 3
_MARINES_TO_ATTACK = 16
_STEPS_PER_SEARCHED_EXPANSION = 448


class MarineBot(BotAI):
    async def on_step(self, iteration: int) -> None:
        if not self.townhalls.ready:
            for unit in self.units:
                unit.attack(self.enemy_start_locations[0])
            return
        command_center = self.townhalls.ready.first
        await self.distribute_workers()
        if command_center.is_idle and self.supply_workers < _MAX_WORKERS and self.can_afford(UnitTypeId.SCV):
            command_center.train(UnitTypeId.SCV)
        await self._build_supply_and_barracks()
        for barracks in self.structures(UnitTypeId.BARRACKS).ready.idle:
            if self.can_afford(UnitTypeId.MARINE):
                barracks.train(UnitTypeId.MARINE)
        self._attack_with_marines()

    async def _build_supply_and_barracks(self) -> None:
        near_base = self.townhalls.ready.first.position.towards(self.game_info.map_center, 6)
        if (
            self.supply_left < 5
            and self.supply_cap < 200
            and not self.already_pending(UnitTypeId.SUPPLYDEPOT)
            and self.can_afford(UnitTypeId.SUPPLYDEPOT)
        ):
            await self.build(UnitTypeId.SUPPLYDEPOT, near=near_base)
        barracks_count = self.structures(UnitTypeId.BARRACKS).amount + self.already_pending(UnitTypeId.BARRACKS)
        if (
            self.structures(UnitTypeId.SUPPLYDEPOT).ready
            and barracks_count < _BARRACKS_WANTED
            and self.can_afford(UnitTypeId.BARRACKS)
        ):
            await self.build(UnitTypeId.BARRACKS, near=near_base.towards(self.game_info.map_center, 4))

    def _attack_with_marines(self) -> None:
        marines = self.units(UnitTypeId.MARINE)
        if marines.amount < _MARINES_TO_ATTACK:
            return
        if self.enemy_structures:
            target = self.enemy_structures.closest_to(marines.center).position
        else:
            expansions = self.expansion_locations_list
            target = expansions[self.state.game_loop // _STEPS_PER_SEARCHED_EXPANSION % len(expansions)]
        for marine in marines.idle:
            marine.attack(target)


BOT = Bot(Race.Terran, MarineBot())
