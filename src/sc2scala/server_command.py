import argparse
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from loguru import logger

from sc2scala.errors import ScalaError

_SERVER_EXTRA_MODULES = frozenset(
    {
        "alembic",
        "fastapi",
        "itsdangerous",
        "jinja2",
        "multipart",
        "python_multipart",
        "sqlalchemy",
        "starlette",
        "uvicorn",
    }
)
_DATA_HELP = "the folder of the server's config, database and files"


def add_server_command(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `server`, and its `backup`, to the commands whose `add_parser` this is."""
    server = add_parser(
        "server",
        help="run a server",
        description=(
            "Runs a Scala server on the data folder given, configured by its server.toml, which is written with "
            "defaults for a server on this machine alone if missing. Needs sc2scala[server]."
        ),
    )
    server.add_argument("--data", type=Path, help=_DATA_HELP)
    actions = server.add_subparsers(dest="server_action", metavar="{backup}")
    backup = actions.add_parser(
        "backup",
        help="copy the server's database, while it runs",
        description=(
            "Copies the server's database whole, as it stands at one moment, even while the server writes to it. The "
            "files beside it never change once written, so a plain copy of the data folder's files keeps them."
        ),
    )
    backup.add_argument("--data", type=Path, default=argparse.SUPPRESS, help=_DATA_HELP)
    backup.add_argument("copy", type=Path, help="the file to write the copy to, which must not exist yet")


def run_server(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.data is None:
        parser.error(f"server needs --data, {_DATA_HELP}")
    if args.server_action == "backup":
        back_up_database(args.data, args.copy)
        print(f"Copied the database of {args.data} to {args.copy}")
        return 0
    try:
        import uvicorn

        from sc2scala.server.app import create_app
        from sc2scala.server.server_config import ServerConfig
    except ModuleNotFoundError as error:
        if (error.name or "").split(".")[0] not in _SERVER_EXTRA_MODULES:
            raise
        raise ScalaError("The server needs sc2scala[server]: install sc2scala with its server extra") from error
    config = ServerConfig.from_data_folder(args.data)
    sign_in = [name for name, on in (("GitHub", config.github_sign_in), ("dev", config.dev_sign_in)) if on]
    logger.info(f"Serving {args.data} at {config.public_url}, sign-in: {', '.join(sign_in) or 'none'}")
    uvicorn.run(create_app(config), host=config.host, port=config.port)
    return 0


def back_up_database(data_folder: Path, copy: Path) -> None:
    """Writes the database of the server whose data folder this is to `copy`, whole, as it stands at one moment.

    Raises `ScalaError` if the folder holds no database, or `copy` exists. The copy is written beside its name first,
    so that a failed backup leaves no file there.
    """
    from sc2scala.server.server_config import DATABASE_FILE_NAME

    database = data_folder / DATABASE_FILE_NAME
    if not database.is_file():
        raise ScalaError(f"{data_folder} holds no server's database, {DATABASE_FILE_NAME}")
    if copy.exists():
        raise ScalaError(f"{copy} exists already: back up to a new file")
    copy.parent.mkdir(parents=True, exist_ok=True)
    partial = copy.with_name(f"{copy.name}.partial")
    partial.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(partial)) as destination:
            source.backup(destination)
        partial.replace(copy)
    except sqlite3.Error as error:
        raise ScalaError(f"Copying the database of {data_folder} failed: {error}") from error
    finally:
        partial.unlink(missing_ok=True)
