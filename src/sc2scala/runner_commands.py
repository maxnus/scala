import argparse
import os
from collections.abc import Callable
from pathlib import Path

import httpx

from sc2scala.aiarena_engine import AiArenaEngine
from sc2scala.match_engine import MatchEngine
from sc2scala.runner import Runner
from sc2scala.runner_credentials import RunnerCredentials, default_runner_file
from sc2scala.scala_client import ScalaClient
from sc2scala.signed_in_client import signed_in_client

_DEFAULT_POLL_SECONDS = 30.0


def add_runner_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `runner` and its subcommands to the commands whose `add_parser` this is."""
    runner = add_parser(
        "runner", help="make this computer a runner machine, and run it", description="Registers and runs runners."
    )
    commands = runner.add_subparsers(dest="runner_command", required=True)
    register = commands.add_parser(
        "register",
        help="register this computer as a runner machine of yours",
        description="Registers this computer as a runner machine of yours, on the server you are signed in to.",
    )
    register.add_argument("name", help="the machine's name: letters, digits and '-', 40 at most")
    register.add_argument("--server", help="the server (default: the last one signed in to)")
    register.add_argument(
        "--ladders", nargs="*", default=[], metavar="LADDER", help="ladders to serve, protected ones among them"
    )
    register.add_argument("--no-public", action="store_true", help="serve the ladders named alone, no public one")
    change = commands.add_parser(
        "set", help="change the ladders a machine of yours serves", description="Changes what it is given alone."
    )
    change.add_argument("name")
    change.add_argument("--server", help="the server (default: the last one signed in to)")
    change.add_argument("--ladders", nargs="*", metavar="LADDER", help="the ladders to serve, in place of the listed")
    public = change.add_mutually_exclusive_group()
    public.add_argument("--public", action="store_true", default=None, help="serve every public ladder")
    public.add_argument("--no-public", action="store_false", dest="public", help="serve no ladder but those listed")
    machines = commands.add_parser("list", help="list your runner machines")
    machines.add_argument("--server", help="the server (default: the last one signed in to)")
    remove = commands.add_parser("remove", help="remove a machine of yours, whose matches are then void")
    remove.add_argument("name")
    remove.add_argument("--server", help="the server (default: the last one signed in to)")
    start = commands.add_parser(
        "start",
        help="play matches on this computer",
        description="Downloads the engine's images if missing, then plays the matches the server draws, one at a time.",
    )
    start.add_argument("--once", action="store_true", help="ask for one match, play it if there is one, and stop")
    start.add_argument(
        "--poll-seconds", type=float, default=_DEFAULT_POLL_SECONDS, help="how long to wait when there is no match"
    )


def run_runner_command(args: argparse.Namespace, *, http: httpx.Client | None, engine: MatchEngine | None) -> int:
    match args.runner_command:
        case "register":
            client = signed_in_client(args.server, http=http)
            machine = client.register_machine(args.name, serves_public=not args.no_public, ladders=args.ladders)
            RunnerCredentials(server_url=client.server_url, machine=machine["name"], token=machine["token"]).save()
            print(f"This computer is {machine['owner']}/{machine['name']} on {client.server_url}: {_serving(machine)}")
            print(f"Its token is kept in {default_runner_file()}; run `sc2scala runner start`")
        case "set":
            client = signed_in_client(args.server, http=http)
            changed = client.change_machine(args.name, serves_public=args.public, ladders=args.ladders)
            print(f"{changed['name']}: {_serving(changed)}")
        case "list":
            for machine in signed_in_client(args.server, http=http).my_machines():
                print(f"{machine['name']}: {_serving(machine)}, last seen {machine['last_seen_at'] or 'never'}")
        case "remove":
            signed_in_client(args.server, http=http).remove_machine(args.name)
            print(f"Removed {args.name}")
        case "start":
            _start(args, http=http, engine=engine)
    return 0


def _start(args: argparse.Namespace, *, http: httpx.Client | None, engine: MatchEngine | None) -> None:
    credentials = RunnerCredentials.load()
    client = ScalaClient(credentials.server_url, credentials.token, http=http)
    work_folder = _data_home() / "work"
    if engine is None:
        aiarena_engine = AiArenaEngine()
        aiarena_engine.pull_images(log_file=work_folder / "pull.log")
        engine = aiarena_engine
    runner = Runner(client, engine, cache_folder=_cache_home(), work_folder=work_folder)
    runner.run(poll_seconds=args.poll_seconds, once=args.once)


def _serving(machine: dict[str, object]) -> str:
    ladders = machine["ladders"]
    named = ", ".join(ladders) if isinstance(ladders, list) and ladders else None
    if machine["serves_public"]:
        return "serves every public ladder" + (f", and {named}" if named else "")
    return f"serves {named}" if named else "serves no ladder"


def _cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "sc2scala"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "sc2scala"
