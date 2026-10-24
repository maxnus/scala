import argparse
import getpass
import os
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

import httpx
from loguru import logger

from sc2scala.aiarena_engine import AiArenaEngine
from sc2scala.match_engine import MatchEngine
from sc2scala.runner import Runner
from sc2scala.runner_config import (
    RunnerConfig,
    default_config_text,
    default_runner_config_file,
    machine_cores_and_memory,
    suggested_runners,
)
from sc2scala.runner_credentials import RunnerCredentials, default_runner_file
from sc2scala.runner_lock import RunnerLock, running_runner
from sc2scala.scala_client import ScalaClient
from sc2scala.signed_in_client import signed_in_client

_DEFAULT_POLL_SECONDS = 30.0
_LOCK_FILE_NAME = "runner.pid"
_SERVICE_NAME = "sc2scala-runner"
_SERVICE_ENVIRONMENT = ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "DOCKER_HOST", "DOCKER_CONTEXT")
"""The variables the service takes from the shell that writes it, so that it finds the same files and Docker."""


def add_runner_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `runner` and its subcommands to the commands whose `add_parser` this is."""
    runner = add_parser(
        "runner", help="make this computer a runner machine, and run it", description="Registers and runs runners."
    )
    commands = runner.add_subparsers(dest="runner_command", required=True)
    register = commands.add_parser(
        "register",
        help="register this computer as a runner machine of yours",
        description="Registers this computer as a runner machine of yours, on the server you are signed in to, and "
        "writes its settings, how many matches it plays at once and when, if it has none.",
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
        description="Downloads the engine's images if missing, then plays the matches the server draws, as many at "
        "once and when the runner's settings say, in runner.toml beside its token. SIGTERM or Ctrl-C stops it once "
        "the matches being played end, and a second stops them as well, as SIGQUIT does.",
    )
    start.add_argument("--once", action="store_true", help="ask for one match, play it if there is one, and stop")
    start.add_argument(
        "--poll-seconds", type=float, default=_DEFAULT_POLL_SECONDS, help="how long to wait when there is no match"
    )
    stop = commands.add_parser(
        "stop",
        help="stop the runner on this computer",
        description="Stops the runner this user runs on this computer, once the matches it plays end.",
    )
    stop.add_argument("--now", action="store_true", help="stop the matches being played as well, which are void")
    commands.add_parser(
        "service",
        help="write a systemd service that runs the runner",
        description=f"Writes the systemd user service {_SERVICE_NAME}, which runs `sc2scala runner start`, and says "
        "how to start it.",
    )


def run_runner_command(args: argparse.Namespace, *, http: httpx.Client | None, engine: MatchEngine | None) -> int:
    match args.runner_command:
        case "register":
            _register(args, http=http)
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
        case "stop":
            return _stop(now=args.now)
        case "service":
            _write_service()
    return 0


def _register(args: argparse.Namespace, *, http: httpx.Client | None) -> None:
    client = signed_in_client(args.server, http=http)
    machine = client.register_machine(args.name, serves_public=not args.no_public, ladders=args.ladders)
    RunnerCredentials(server_url=client.server_url, machine=machine["name"], token=machine["token"]).save()
    print(f"This computer is {machine['owner']}/{machine['name']} on {client.server_url}: {_serving(machine)}")
    print(f"Its token is kept in {default_runner_file()}")
    config_file = default_runner_config_file()
    cores, memory_bytes = machine_cores_and_memory()
    if not config_file.exists():
        config_file.write_text(default_config_text(cores=cores, memory_bytes=memory_bytes), encoding="utf-8")
    config = RunnerConfig.load(config_file)
    print(
        f"It plays {config.runners} {'match' if config.runners == 1 else 'matches'} at once, "
        f"{'in its windows' if config.schedule.windows else 'at any hour'}, as {config_file} says; "
        f"about {suggested_runners(cores=cores, memory_bytes=memory_bytes)} fit this computer"
    )
    print("Run `sc2scala runner start`, or `sc2scala runner service` to run it as a service")


def _start(args: argparse.Namespace, *, http: httpx.Client | None, engine: MatchEngine | None) -> None:
    credentials = RunnerCredentials.load()
    config = RunnerConfig.load()
    client = ScalaClient(credentials.server_url, credentials.token, http=http)
    work_folder = _data_home() / "work"
    with RunnerLock(_data_home() / _LOCK_FILE_NAME):
        if engine is None:
            aiarena_engine = AiArenaEngine()
            aiarena_engine.pull_images(log_file=work_folder / "pull.log")
            engine = aiarena_engine
        runner = Runner(
            client, engine, cache_folder=_cache_home(), work_folder=work_folder, cache_bytes=config.cache_bytes
        )
        if args.once:
            runner.remove_leftovers()
            runner.play_next()
            return
        logger.info(
            f"{credentials.machine} plays up to {config.runners} {'match' if config.runners == 1 else 'matches'} at "
            f"once for {credentials.server_url}. Ctrl-C or `sc2scala runner stop` stops it once the matches being "
            "played end, and a second time stops them too"
        )
        with _stopped_by_signals(runner):
            runner.run(poll_seconds=args.poll_seconds, workers=config.runners, schedule=config.schedule)


@contextmanager
def _stopped_by_signals(runner: Runner) -> Iterator[None]:
    """Lets SIGTERM and SIGINT stop `runner` once its matches end, a second stop them too, and SIGQUIT stop them at
    once, while the block runs."""

    def stop(signal_number: int, frame: FrameType | None) -> None:
        if runner.stopping:
            runner.stop_now()
        else:
            runner.stop()

    def stop_now(signal_number: int, frame: FrameType | None) -> None:
        runner.stop_now()

    handlers = {signal.SIGTERM: stop, signal.SIGINT: stop, signal.SIGQUIT: stop_now}
    previous = {number: signal.signal(number, handler) for number, handler in handlers.items()}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _stop(*, now: bool) -> int:
    """Signals this user's runner to stop, or to stop now; returns 1 if none is running."""
    process = running_runner(_data_home() / _LOCK_FILE_NAME)
    try:
        if process is None:
            raise ProcessLookupError
        os.kill(process, signal.SIGQUIT if now else signal.SIGTERM)
    except ProcessLookupError:
        print("No runner is running")
        return 1
    if now:
        print(f"Asked the runner, process {process}, to stop the matches it plays, which are void, and itself")
    else:
        print(
            f"Asked the runner, process {process}, to stop once the matches it plays end; "
            "`sc2scala runner stop --now` stops them as well"
        )
    return 0


def _write_service() -> None:
    unit_file = _config_home() / "systemd" / "user" / f"{_SERVICE_NAME}.service"
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    environment = {name: os.environ[name] for name in _SERVICE_ENVIRONMENT if os.environ.get(name)}
    unit_file.write_text(_service_unit(python=sys.executable, environment=environment), encoding="utf-8")
    print(f"Wrote {unit_file}. To start it now, and whenever you log in:")
    print("  systemctl --user daemon-reload")
    print(f"  systemctl --user enable --now {_SERVICE_NAME}")
    print(f"To run it while you are logged out, and from boot: sudo loginctl enable-linger {getpass.getuser()}")
    print(f"Its log: journalctl --user --unit {_SERVICE_NAME} --follow")


def _service_unit(*, python: str, environment: dict[str, str]) -> str:
    """The systemd user service that runs `sc2scala runner start` with `python`, and with `environment` set."""
    environment_lines = "".join(
        f"Environment={_systemd_quoted(f'{name}={value}')}\n" for name, value in environment.items()
    )
    return f"""\
[Unit]
Description=sc2scala runner, playing StarCraft II matches between bots for a Scala server
After=docker.service

[Service]
ExecStart={_systemd_quoted(python)} -m sc2scala runner start
Environment=PYTHONUNBUFFERED=1
{environment_lines}Restart=on-failure
RestartSec=60
# A stop lets the matches being played end, each within its ladder's real-time limit, so systemd signals the runner
# alone and waits for it; `sc2scala runner stop --now` stops the matches as well.
KillMode=mixed
TimeoutStopSec=infinity

[Install]
WantedBy=default.target
"""


def _systemd_quoted(argument: str) -> str:
    """`argument` as one argument of a systemd unit's command line or setting, its specifiers escaped."""
    escaped = argument.replace("%", "%%").replace("$", "$$")
    if not any(character in escaped for character in " \t\"'\\"):
        return escaped
    return '"' + escaped.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _serving(machine: dict[str, object]) -> str:
    ladders = machine["ladders"]
    named = ", ".join(ladders) if isinstance(ladders, list) and ladders else None
    if machine["serves_public"]:
        return "serves every public ladder" + (f", and {named}" if named else "")
    return f"serves {named}" if named else "serves no ladder"


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "sc2scala"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "sc2scala"
