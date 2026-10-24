import argparse
import getpass
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sc2scala.errors import ScalaError
from sc2scala.scala_client import ScalaClient
from sc2scala.signed_in_client import signed_in_client


def add_ladder_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `ladder` and its subcommands to the commands whose `add_parser` this is."""
    ladder = add_parser(
        "ladder", help="make, join and enter ladders", description="Makes, changes, joins and enters ladders."
    )
    ladder.add_argument("--server", help="the server (default: the last one signed in to)")
    commands = ladder.add_subparsers(dest="ladder_command", required=True)
    create = commands.add_parser("create", help="make a ladder", description="Makes a ladder of the maps given.")
    create.add_argument("name", help="the ladder's name: letters, digits and '-', 40 at most")
    create.add_argument("--maps", nargs="+", type=Path, required=True, metavar="MAP", help=".SC2Map files")
    create.add_argument("--protect", action="store_true", help="protect it with a password, asked for")
    _add_limit_argument(create)
    commands.add_parser("list", help="list the server's ladders")
    show = commands.add_parser("show", help="show a ladder: its maps, limits and bots")
    show.add_argument("name")
    change = commands.add_parser("set", help="change your ladder's password or limits")
    change.add_argument("name")
    protection = change.add_mutually_exclusive_group()
    protection.add_argument(
        "--protect", action="store_true", help="set a new password, asked for; whoever joined before stays a member"
    )
    protection.add_argument("--unprotect", action="store_true", help="remove the password")
    _add_limit_argument(change)
    join = commands.add_parser("join", help="join a protected ladder with its password")
    join.add_argument("name")
    for subcommand, help_text in [
        ("enter", "enter your bot in a ladder"),
        ("withdraw", "take a bot out of a ladder"),
        ("pause", "pause your bot in a ladder"),
        ("resume", "let your paused bot play again"),
    ]:
        entry = commands.add_parser(subcommand, help=help_text)
        entry.add_argument("name", help="the ladder")
        entry.add_argument("bot")
    add_map = commands.add_parser("add-map", help="add a map to your ladder")
    add_map.add_argument("name")
    add_map.add_argument("map_file", type=Path, metavar="MAP", help="an .SC2Map file")
    standings = commands.add_parser("standings", help="show a ladder's bots by rating")
    standings.add_argument("name")
    matches = commands.add_parser("matches", help="list a ladder's latest matches")
    matches.add_argument("name")
    matches.add_argument("--limit", type=int, default=20, help="how many (default: 20)")
    remove_map = commands.add_parser("remove-map", help="take a map out of your ladder")
    remove_map.add_argument("name")
    remove_map.add_argument("map_name", metavar="MAP", help="the map's name, without .SC2Map")
    runners = commands.add_parser("runners", help="list the runner machines that played a ladder's matches")
    runners.add_argument("name")
    bar = commands.add_parser(
        "bar-runner",
        help="bar a runner machine from your ladder",
        description="Bars a runner machine from your ladder's matches, and voids the matches it plays there and its "
        "results there, unless --keep-results; the ratings are fitted again without them.",
    )
    bar.add_argument("name", help="the ladder")
    bar.add_argument("runner", type=_runner_from_text, metavar="OWNER/MACHINE", help="as `ladder runners` lists it")
    bar.add_argument(
        "--keep-results", action="store_true", help="keep the results it reported, for a runner barred for its speed"
    )
    unbar = commands.add_parser("unbar-runner", help="let a barred runner machine play your ladder again")
    unbar.add_argument("name", help="the ladder")
    unbar.add_argument("runner", type=_runner_from_text, metavar="OWNER/MACHINE")


def run_ladder_command(args: argparse.Namespace, *, http: httpx.Client | None) -> int:
    client = signed_in_client(args.server, http=http)
    match args.ladder_command:
        case "create":
            password = _new_password() if args.protect else None
            print(
                _description_of(
                    client.create_ladder(args.name, map_files=args.maps, password=password, limits=dict(args.limit))
                )
            )
        case "list":
            for ladder in client.ladders():
                print(_summary_line(ladder))
        case "show":
            print(_description_of(client.ladder(args.name)))
        case "set":
            changed = client.change_ladder(
                args.name,
                new_password=_new_password() if args.protect else None,
                remove_password=args.unprotect,
                limits=dict(args.limit),
            )
            print(_description_of(changed))
        case "join":
            print(_description_of(_join(client, args.name)))
        case "enter":
            print(_description_of(client.enter_bot(args.name, args.bot)))
        case "withdraw":
            print(_description_of(client.withdraw_bot(args.name, args.bot)))
        case "pause":
            print(_description_of(client.pause_entry(args.name, args.bot)))
        case "resume":
            print(_description_of(client.resume_entry(args.name, args.bot)))
        case "add-map":
            print(_description_of(client.add_map(args.name, args.map_file)))
        case "remove-map":
            print(_description_of(client.remove_map(args.name, args.map_name)))
        case "standings":
            for place, standing in enumerate(client.ladder_standings(args.name), start=1):
                print(_standing_line(place, standing))
        case "matches":
            for played in client.ladder_matches(args.name, limit=args.limit):
                print(_match_line(played))
        case "runners":
            for runner in client.ladder_runners(args.name):
                print(_runner_line(runner))
        case "bar-runner":
            barred = client.bar_runner(args.name, args.runner, keep_results=args.keep_results)
            voided = barred["results_voided"]
            kept = (
                "its results kept" if args.keep_results else f"{voided} {'result' if voided == 1 else 'results'} void"
            )
            print(f"Barred {barred['runner']} from {args.name}, {kept}")
        case "unbar-runner":
            unbarred = client.unbar_runner(args.name, args.runner)
            print(f"{unbarred['runner']} may play {args.name} again; what its bar voided stays void")
    return 0


def _add_limit_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit",
        action="append",
        type=_limit_from_text,
        default=[],
        metavar="NAME=VALUE",
        help="a limit of its matches, such as bot_memory_mib=2048; repeat for several",
    )


def _limit_from_text(text: str) -> tuple[str, str]:
    name, separator, value = text.partition("=")
    if not separator or not name or not value:
        raise argparse.ArgumentTypeError(f"A limit is NAME=VALUE, not {text!r}")
    return name.strip(), value.strip()


def _runner_from_text(text: str) -> str:
    owner, separator, machine = text.partition("/")
    if not separator or not owner or not machine or "/" in machine:
        raise argparse.ArgumentTypeError(f"A runner is OWNER/MACHINE, not {text!r}")
    return text


def _new_password() -> str:
    password = getpass.getpass("Password: ")
    if not password or password != getpass.getpass("The same again: "):
        raise ScalaError("The passwords are empty, or differ")
    return password


def _join(client: ScalaClient, name: str) -> dict[str, Any]:
    summary = next((ladder for ladder in client.ladders() if ladder["name"].lower() == name.lower()), None)
    if summary is None:
        raise ScalaError(f"No ladder is named {name}")
    needs_password = summary["protected"] and not summary.get("member", False)
    return client.join_ladder(
        name, password=getpass.getpass(f"{summary['name']}'s password: ") if needs_password else ""
    )


def _summary_line(ladder: dict[str, Any]) -> str:
    access = "protected" if ladder["protected"] else "public"
    if "bots" not in ladder:
        return f"{ladder['name']}: {access}, by {ladder['owner']}"
    member = ", you are a member" if ladder["protected"] and ladder["member"] else ""
    return f"{ladder['name']}: {access}, by {ladder['owner']}, {ladder['bots']} bots on {ladder['maps']} maps{member}"


def _description_of(ladder: dict[str, Any]) -> str:
    lines = [
        _summary_line({**ladder, "bots": len(ladder["entries"]), "maps": len(ladder["maps"])}),
        "Maps: " + ", ".join(map_file["name"] for map_file in ladder["maps"]),
        "Limits: "
        + ", ".join(
            f"{name}={value}" for name, value in {**ladder["game_limits"], **ladder["resource_limits"]}.items()
        ),
    ]
    for entry in ladder["entries"]:
        paused = f", paused: {entry['paused_because']}" if entry["paused"] else ""
        lines.append(f"  {entry['bot']}, by {entry['author']}, version {entry['version']}{paused}")
    return "\n".join(lines)


def _match_line(played: dict[str, Any]) -> str:
    players = " vs ".join(f"{player['bot']} ({player['race']}, v{player['version']})" for player in played["players"])
    if played["status"] == "void":
        outcome = f"void, {played['result']} reported" if played["result"] else "void"
    else:
        outcome = played["result"] or played["status"]
    if played["void_because"]:
        outcome += f": {played['void_because']}"
    return f"{played['id']}: {players} on {played['map']}, {outcome}, by {played['runner'] or 'a removed runner'}"


def _runner_line(runner: dict[str, Any]) -> str:
    counts = f"{runner['finished']} finished, {runner['void']} void, {runner['being_played']} being played"
    last = f", last drawn {_local_time(runner['last_drawn_at'])}" if runner["last_drawn_at"] else ""
    barred = f", barred since {_local_time(runner['barred_at'])}" if runner["barred_at"] else ""
    return f"{runner['runner']}: {counts}{last}{barred}"


def _local_time(moment: str) -> str:
    """A moment the server gives, in ISO 8601, as this computer's local time to the minute."""
    return f"{datetime.fromisoformat(moment).astimezone():%Y-%m-%d %H:%M}"


def _standing_line(place: int, standing: dict[str, Any]) -> str:
    rating = f"{standing['rating']} ± {standing['interval']}" if standing["rating"] is not None else "unrated"
    record = f"{standing['wins']}-{standing['losses']}-{standing['ties']}"
    paused = ", paused" if standing["paused"] else ""
    return (
        f"{place:>3}. {standing['bot']} ({standing['race']}, v{standing['version']}, by {standing['author']}): "
        f"{rating}, {standing['games']} games, {record}, {standing['crashes']} crashes{paused}"
    )
