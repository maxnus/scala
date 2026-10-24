import json
import os
from dataclasses import dataclass
from pathlib import Path

from sc2scala.client_credentials import default_credentials_file
from sc2scala.errors import ScalaError

_RUNNER_FILE_NAME = "runner.json"


@dataclass(frozen=True)
class RunnerCredentials:
    """The runner machine this computer is, on which server, with the machine's token.

    Kept in `runner.json` beside the CLI's credentials, which this user alone may read.
    """

    server_url: str
    machine: str
    token: str

    @classmethod
    def load(cls, file: Path | None = None) -> "RunnerCredentials":
        """The runner credentials in `file`, by default the user's; raises `ScalaError` if there are none."""
        file = file or default_runner_file()
        if not file.exists():
            raise ScalaError("This computer is no runner machine yet: run `sc2scala runner register <name>`")
        content = json.loads(file.read_text(encoding="utf-8"))
        return cls(server_url=content["server_url"], machine=content["machine"], token=content["token"])

    def save(self, file: Path | None = None) -> Path:
        file = file or default_runner_file()
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(file, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as runner:
            json.dump({"server_url": self.server_url, "machine": self.machine, "token": self.token}, runner, indent=2)
        return file


def default_runner_file() -> Path:
    return default_credentials_file().with_name(_RUNNER_FILE_NAME)
