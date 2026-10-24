import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from loguru import logger

from sc2scala.aiarena_engine import AiArenaEngine
from sc2scala.bot import Bot
from sc2scala.errors import EngineError
from sc2scala.match import Match
from sc2scala.match_engine import MatchEngine
from sc2scala.match_result import MatchResult
from sc2scala.race import Race

_RESULT_FILE_NAME = "result.json"
_DEFAULT_OUTPUT_ROOT = Path("matches")


def main(argv: Sequence[str] | None = None, *, engine: MatchEngine | None = None) -> int:
    """Runs the `sc2scala` command with `argv`, by default the process's arguments, and returns its exit code.

    `engine` plays the matches, by default an `AiArenaEngine`.
    """
    parser = _command_line_parser()
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    try:
        match = _match_from_arguments(args)
    except ValueError as error:
        parser.error(str(error))
    output_folder = args.output or _default_output_folder(match)
    try:
        result = (engine or AiArenaEngine()).play(match, output_folder=output_folder)
    except EngineError as error:
        logger.error(str(error))
        return 1
    _write_result_file(match, result, output_folder / _RESULT_FILE_NAME)
    print(_summary_of(match, result, output_folder))
    return 0


def _command_line_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sc2scala", description="StarCraft II ladders between bots.")
    commands = parser.add_subparsers(dest="command", required=True)
    play = commands.add_parser(
        "play",
        help="play one match on this machine",
        description="Plays one match between two bots on this machine, and writes its result, replay and logs.",
    )
    play.add_argument("bots", nargs=2, type=Path, metavar="BOT_FOLDER", help="a folder named for its bot")
    play.add_argument(
        "--map", required=True, type=Path, dest="map_file", metavar="MAP", help="the .SC2Map file to play on"
    )
    play.add_argument(
        "--races",
        nargs=2,
        type=_race_from_text,
        default=[Race.RANDOM, Race.RANDOM],
        metavar="RACE",
        help="each bot's race: T, Z, P or R, or its name (default: R R)",
    )
    play.add_argument(
        "--output",
        type=Path,
        help=f"the folder to write into, which must be empty (default: {_DEFAULT_OUTPUT_ROOT}/<time>-<bot>-vs-<bot>)",
    )
    return parser


def _race_from_text(text: str) -> Race:
    for race in Race:
        if text.upper() in (race.value, race.name):
            return race
    raise argparse.ArgumentTypeError(f"{text!r} is no race: T, Z, P or R, or its name")


def _match_from_arguments(args: argparse.Namespace) -> Match:
    """The match the arguments of `play` name; raises `ValueError` for a folder or a map that is not there."""
    for folder in args.bots:
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder")
    if not args.map_file.is_file():
        raise ValueError(f"{args.map_file} is not a file")
    player_1 = Bot.from_folder(args.bots[0], race=args.races[0])
    player_2 = Bot.from_folder(args.bots[1], race=args.races[1])
    return Match(player_1=player_1, player_2=player_2, map_file=args.map_file)


def _default_output_folder(match: Match) -> Path:
    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _DEFAULT_OUTPUT_ROOT / f"{started}-{match.player_1.name}-vs-{match.player_2.name}"


def _write_result_file(match: Match, result: MatchResult, result_file: Path) -> None:
    players = [
        {"name": bot.name, "race": bot.race.value, "type": bot.bot_type.value, "average_step_time": step_time}
        for bot, step_time in zip((match.player_1, match.player_2), result.average_step_times, strict=True)
    ]
    content = {
        "result": result.code.value,
        "map": match.map_name,
        "game_steps": result.game_steps,
        "game_seconds": round(result.game_seconds, 1),
        "real_time_seconds": round(result.real_time_seconds, 1),
        "players": players,
        "replay": result.replay_file.name if result.replay_file else None,
    }
    result_file.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def _summary_of(match: Match, result: MatchResult, output_folder: Path) -> str:
    return (
        f"{match.player_1.name} vs {match.player_2.name} on {match.map_name}: {result.code.value} after "
        f"{_minutes_and_seconds(result.game_seconds)} of game time, {_minutes_and_seconds(result.real_time_seconds)} "
        f"of real time. Written to {output_folder}"
    )


def _minutes_and_seconds(seconds: float) -> str:
    minutes, remaining_seconds = divmod(round(seconds), 60)
    return f"{minutes}:{remaining_seconds:02d}"
