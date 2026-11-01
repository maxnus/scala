"""Plays the match its one argument, a config file, describes, as Scala's engine starts it in the match's container:
`python -m match_controller CONFIG`. Writes how the match ended to the config's result file, and exits with 0 whatever
the outcome, or with 1 if the controller itself failed, leaving no result."""

import asyncio
import json
import logging
import sys
from pathlib import Path

import google.protobuf
import websockets.version

from .controller_config import ControllerConfig
from .match_controller import MatchController


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("Usage: python -m match_controller CONFIG", file=sys.stderr)
        return 2
    config = ControllerConfig.from_file(Path(arguments[0]))
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.log_file is not None:
        handlers.append(logging.FileHandler(config.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.info(
        f"protobuf {google.protobuf.__version__} from {Path(google.protobuf.__file__).parent}, "
        f"websockets {websockets.version.version}"
    )
    try:
        result = asyncio.run(MatchController(config).play())
    except Exception:
        logging.exception("The match controller failed")
        return 1
    Path(config.result_file).write_text(json.dumps(result.to_json()) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
