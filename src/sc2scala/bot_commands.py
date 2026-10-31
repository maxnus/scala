import argparse
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from sc2scala.errors import ScalaError
from sc2scala.removal_commands import confirm_at_terminal, removal_line
from sc2scala.scala_client import ScalaClient
from sc2scala.signed_in_client import signed_in_client

_SKIPPED_FOLDERS = frozenset({"__pycache__", ".git"})


def add_bot_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `bot` and its subcommands to the commands whose `add_parser` this is."""
    bot = add_parser("bot", help="upload and look at your bots", description="Uploads, lists and removes your bots.")
    bot.add_argument("--server", help="the server (default: the last one signed in to)")
    commands = bot.add_subparsers(dest="bot_command", required=True)
    upload = commands.add_parser(
        "upload",
        help="upload a version of a bot",
        description=(
            "Uploads a bot's folder, or its zip in AI Arena's layout, as the bot's next version; a bot is made at its "
            "first upload, which needs its race."
        ),
    )
    upload.add_argument("name", help="the bot's name")
    upload.add_argument("path", type=Path, help="the bot's folder, or its zip, with the entry point at its root")
    upload.add_argument("--race", help="T, Z, P or R, or its name; needed at the first upload")
    commands.add_parser("list", help="list your bots")
    show = commands.add_parser("show", help="show a bot and its versions")
    show.add_argument("name")
    remove = commands.add_parser(
        "remove",
        help="delete your bot, or retire it if it was drawn for a match",
        description=(
            "Deletes your bot, with its versions, if it was never drawn for a match, and frees its name; or else "
            "retires it: it is taken out of every ladder and takes no new version, and its matches, ratings and name "
            "stay."
        ),
    )
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="remove it without asking")


def run_bot_command(args: argparse.Namespace, *, http: httpx.Client | None) -> int:
    client = signed_in_client(args.server, http=http)
    match args.bot_command:
        case "upload":
            uploaded = _upload(client, args.name, args.path, race=args.race)
            version = uploaded["version"]
            what = f"version {version['number']}" if uploaded["new"] else f"unchanged, version {version['number']}"
            print(f"{uploaded['bot']['name']}: {what}, a {version['type']} bot of {version['size_bytes']} bytes")
        case "list":
            for bot in client.my_bots():
                print(_bot_line(bot))
        case "remove":
            confirm_at_terminal(
                f"Remove {args.name}? It is deleted if it was never drawn for a match, or else retired, its matches "
                "kept.",
                assume_yes=args.yes,
            )
            removed = client.remove_bot(args.name)
            print(removal_line(removed, drawn="was drawn for", stays="it plays no more, and its matches stay"))
        case "show":
            bot = client.bot(args.name)
            print(_bot_line(bot))
            for version in bot["versions"]:
                print(
                    f"  version {version['number']}: {version['type']}, {version['size_bytes']} bytes, "
                    f"uploaded {version['uploaded_at'][:16].replace('T', ' ')}, sha256 {version['sha256'][:12]}"
                )
    return 0


def _upload(client: ScalaClient, name: str, path: Path, *, race: str | None) -> dict[str, Any]:
    if path.is_file():
        return client.upload_bot_version(name, path, race=race)
    if not path.is_dir():
        raise ScalaError(f"{path} is neither a bot's folder nor its zip")
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / f"{name}.zip"
        _zip_folder(path, archive)
        return client.upload_bot_version(name, archive, race=race)


def _zip_folder(folder: Path, archive: Path) -> None:
    """Zips `folder`'s files, but for Python's caches and Git's, with the folder's own files at the zip's root."""
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if path.is_file() and not path.is_symlink() and not _SKIPPED_FOLDERS.intersection(relative.parts):
                zipped.write(path, relative.as_posix())


def _bot_line(bot: dict[str, Any]) -> str:
    version = bot["latest_version"]
    retired = ", retired" if bot.get("retired_at") else ""
    return (
        f"{bot['name']}: {bot['race']}, by {bot['author']}, "
        + (f"version {version}" if version else "no version")
        + retired
    )
