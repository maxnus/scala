import argparse
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from sc2scala.errors import ScalaError


def add_server_command(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Adds `server` to the commands whose `add_parser` this is."""
    server = add_parser(
        "server",
        help="run a server",
        description=(
            "Runs a Scala server on the data folder given, configured by its server.toml, which is written with "
            "defaults for a server on this machine alone if missing. Needs sc2scala[server]."
        ),
    )
    server.add_argument(
        "--data", required=True, type=Path, help="the folder of the server's config, database and files"
    )


def run_server(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from sc2scala.server.app import create_app
        from sc2scala.server.server_config import ServerConfig
    except ImportError as error:
        raise ScalaError("The server needs sc2scala[server]: install sc2scala with its server extra") from error
    config = ServerConfig.from_data_folder(args.data)
    sign_in = [name for name, on in (("GitHub", config.github_sign_in), ("dev", config.dev_sign_in)) if on]
    logger.info(f"Serving {args.data} at {config.public_url}, sign-in: {', '.join(sign_in) or 'none'}")
    uvicorn.run(create_app(config), host=config.host, port=config.port)
    return 0
