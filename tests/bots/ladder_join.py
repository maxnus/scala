"""Joins the game a ladder created, with python-sc2, which AI Arena's bot image holds.

Adapted from python-sc2's examples/competitive/__init__.py (MIT License, Copyright (c) 2017 Hannes Karppila).
"""

import argparse
import asyncio

import aiohttp
from sc2.client import Client
from sc2.main import _play_game
from sc2.player import Bot
from sc2.portconfig import Portconfig


def run_ladder_game(bot: Bot) -> None:
    """Plays the ladder's game given on the command line as `bot`, to its end."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--GamePort", type=int)
    parser.add_argument("--StartPort", type=int)
    parser.add_argument("--LadderServer", default="127.0.0.1")
    parser.add_argument("--OpponentId")
    parser.add_argument("--RealTime", action="store_true")
    args, _ = parser.parse_known_args()
    bot.ai.opponent_id = args.OpponentId
    ports = [args.StartPort + offset for offset in range(1, 6)]
    portconfig = Portconfig()
    portconfig.server = [ports[1], ports[2]]
    portconfig.players = [[ports[3], ports[4]]]
    result = asyncio.run(_join_game(args.LadderServer, args.GamePort, bot, args.RealTime, portconfig))
    print(f"{result} against {args.OpponentId}")


async def _join_game(host: str, port: int, bot: Bot, realtime: bool, portconfig: Portconfig) -> object:
    async with aiohttp.ClientSession() as session:
        connection = await session.ws_connect(f"ws://{host}:{port}/sc2api", timeout=120)
        try:
            return await _play_game(bot, Client(connection), realtime, portconfig)
        finally:
            await connection.close()
