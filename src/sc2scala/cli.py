import argparse
import sys
from collections.abc import Sequence

import httpx
from loguru import logger

from sc2scala.account_commands import add_account_commands, run_login, run_logout, run_whoami
from sc2scala.errors import ScalaError
from sc2scala.match_engine import MatchEngine
from sc2scala.play_command import add_play_command, run_play
from sc2scala.server_command import add_server_command, run_server


def main(
    argv: Sequence[str] | None = None, *, engine: MatchEngine | None = None, http: httpx.Client | None = None
) -> int:
    """Runs the `sc2scala` command with `argv`, by default the process's arguments, and returns its exit code.

    `engine` plays the matches, by default an `AiArenaEngine`, and `http` calls the servers.
    """
    parser = _command_line_parser()
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    try:
        match args.command:
            case "play":
                return run_play(args, parser, engine=engine)
            case "login":
                return run_login(args, http=http)
            case "whoami":
                return run_whoami(args, http=http)
            case "logout":
                return run_logout(args)
            case "server":
                return run_server(args)
            case _:
                parser.error(f"unknown command {args.command}")
    except ScalaError as error:
        logger.error(str(error))
        return 1


def _command_line_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sc2scala", description="StarCraft II ladders between bots.")
    commands = parser.add_subparsers(dest="command", required=True)
    add_play_command(commands.add_parser)
    add_account_commands(commands.add_parser)
    add_server_command(commands.add_parser)
    return parser
