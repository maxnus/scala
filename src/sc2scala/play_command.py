import argparse
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from loguru import logger

from sc2scala.bot import Bot
from sc2scala.computer import Computer, is_computer_name
from sc2scala.engine_choice import DEFAULT_ENGINE, ENGINE_NAMES, engine_named
from sc2scala.errors import EngineError
from sc2scala.match import Match
from sc2scala.match_engine import MatchEngine
from sc2scala.match_result import MatchResult
from sc2scala.race import Race

_RESULT_FILE_NAME = "result.json"
_DEFAULT_OUTPUT_ROOT = Path("matches")


def add_play_command(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `play` to the commands whose `add_parser` this is."""
    play = add_parser(
        "play",
        help="play one match on this machine",
        description="Plays one match between two bots, or a bot and the game's built-in computer, on this machine, and "
        "writes its result, replay and logs.",
    )
    play.add_argument(
        "players",
        nargs=2,
        type=Path,
        metavar=("BOT_FOLDER", "OPPONENT"),
        help="a folder named for its bot, and its opponent: another, or the computer, as "
        "Computer-<difficulty>-<race>-<build>, such as Computer-VeryHard-Zerg-Rush, which Scala's engine alone plays",
    )
    play.add_argument(
        "--map", required=True, type=Path, dest="map_file", metavar="MAP", help="the .SC2Map file to play on"
    )
    play.add_argument(
        "--races",
        nargs=2,
        type=_race_from_text,
        default=[Race.RANDOM, Race.RANDOM],
        metavar="RACE",
        help="each bot's race: T, Z, P or R, or its name (default: R R); the computer's is in its name",
    )
    play.add_argument(
        "--engine",
        choices=ENGINE_NAMES,
        default=DEFAULT_ENGINE,
        help=f"the engine that plays it: AI Arena's, or Scala's own, being tried (default: {DEFAULT_ENGINE})",
    )
    play.add_argument(
        "--output",
        type=Path,
        help=f"the folder to write into, which must be empty (default: {_DEFAULT_OUTPUT_ROOT}/<time>-<bot>-vs-<bot>)",
    )


def run_play(args: argparse.Namespace, parser: argparse.ArgumentParser, *, engine: MatchEngine | None) -> int:
    """Plays the match `args` names with `engine`, by default the one `--engine` names, and returns the exit code."""
    try:
        match = _match_from_arguments(args)
        _check_is_empty_or_missing(args.output)
    except ValueError as error:
        parser.error(str(error))
    output_folder = args.output or _new_default_output_folder(match)
    try:
        result = (engine or engine_named(args.engine)).play(match, output_folder=output_folder)
    except EngineError as error:
        logger.error(str(error))
        return 1
    _write_result_file(match, result, output_folder / _RESULT_FILE_NAME)
    print(_summary_of(match, result, output_folder))
    return 0


def _race_from_text(text: str) -> Race:
    try:
        return Race.from_text(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _match_from_arguments(args: argparse.Namespace) -> Match:
    """The match the arguments of `play` name; raises `ValueError` for a folder or a map that is not there, or an
    opponent that is neither a folder nor a computer."""
    bot_folder, opponent = args.players
    plays_computer = not opponent.is_dir() and is_computer_name(str(opponent))
    for folder in [bot_folder] if plays_computer else [bot_folder, opponent]:
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder")
    if not args.map_file.is_file():
        raise ValueError(f"{args.map_file} is not a file")
    player_1 = Bot.from_folder(bot_folder, race=args.races[0])
    if plays_computer:
        return Match(player_1=player_1, player_2=Computer.from_name(str(opponent)), map_file=args.map_file)
    return Match(player_1=player_1, player_2=Bot.from_folder(opponent, race=args.races[1]), map_file=args.map_file)


def _check_is_empty_or_missing(output_folder: Path | None) -> None:
    """Raises `ValueError` if `output_folder` is a file or a folder that holds anything."""
    if output_folder is None or not output_folder.exists():
        return
    if not output_folder.is_dir() or any(output_folder.iterdir()):
        raise ValueError(f"{output_folder} is not an empty folder")


def _new_default_output_folder(match: Match) -> Path:
    """Creates and returns `<time>-<bot>-vs-<bot>` under the default root, with `-2`, `-3` and so on appended while
    another run has made that folder already."""
    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = f"{started}-{match.player_1.name}-vs-{match.player_2.name}"
    attempt = 1
    while True:
        output_folder = _DEFAULT_OUTPUT_ROOT / (base_name if attempt == 1 else f"{base_name}-{attempt}")
        try:
            output_folder.mkdir(parents=True)
        except FileExistsError:
            attempt += 1
            continue
        return output_folder


def _write_result_file(match: Match, result: MatchResult, result_file: Path) -> None:
    players = [
        {
            "name": player.name,
            "race": player.race.value,
            "type": "computer" if isinstance(player, Computer) else player.bot_type.value,
            "average_step_time": step_time,
        }
        for player, step_time in zip((match.player_1, match.player_2), result.average_step_times, strict=True)
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
