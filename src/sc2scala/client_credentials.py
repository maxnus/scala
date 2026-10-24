import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from sc2scala.errors import ScalaError

_CREDENTIALS_FILE_NAME = "credentials.json"


class NotSignedInError(ScalaError):
    """The CLI holds no token for the server asked for."""


@dataclass
class ClientCredentials:
    """The servers this user signed in to with the CLI, each with its token, and the one the CLI talks to by default.

    Kept in `credentials.json` in the user's config folder, `$XDG_CONFIG_HOME/sc2scala` or `~/.config/sc2scala`, which
    this user alone may read.
    """

    file: Path
    default_server: str | None = None
    tokens: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, file: Path | None = None) -> "ClientCredentials":
        """The credentials in `file`, by default the user's; none if it does not exist."""
        file = file or default_credentials_file()
        if not file.exists():
            return cls(file=file)
        content = json.loads(file.read_text(encoding="utf-8"))
        return cls(file=file, default_server=content.get("default_server"), tokens=dict(content.get("tokens", {})))

    def save(self) -> None:
        self.file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(self.file, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as credentials:
            json.dump({"default_server": self.default_server, "tokens": self.tokens}, credentials, indent=2)

    def add(self, server_url: str, token: str) -> None:
        """Keeps `token` for `server_url`, which becomes the default server."""
        server_url = normalized_server_url(server_url)
        self.tokens[server_url] = token
        self.default_server = server_url

    def remove(self, server_url: str) -> None:
        """Forgets the token for `server_url`; another server signed in to becomes the default, if any is."""
        server_url = normalized_server_url(server_url)
        self.tokens.pop(server_url, None)
        if self.default_server == server_url:
            self.default_server = next(iter(self.tokens), None)

    def server_and_token(self, server_url: str | None = None) -> tuple[str, str]:
        """`server_url`, by default the default server, with its token; raises `NotSignedInError` if there is none."""
        server_url = normalized_server_url(server_url) if server_url else self.default_server
        if server_url is None or server_url not in self.tokens:
            raise NotSignedInError(f"Not signed in to {server_url or 'any server'}: run `sc2scala login <server>`")
        return server_url, self.tokens[server_url]


def default_credentials_file() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "sc2scala" / _CREDENTIALS_FILE_NAME


def normalized_server_url(server_url: str) -> str:
    """`server_url` without the slash it may end with."""
    return server_url.rstrip("/")
