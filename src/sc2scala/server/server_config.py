import os
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

CONFIG_FILE_NAME = "server.toml"
_SECRET_KEY_FILE_NAME = "secret_key"
_DATABASE_FILE_NAME = "scala.db"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_DEFAULT_CONFIG = """\
# Scala's server. Its pages are served at public_url, to which GitHub's sign-in returns.
host = "127.0.0.1"
port = 8000
public_url = "http://127.0.0.1:8000"

# Sign-in by any name, for a server on this machine alone; refused unless host is local.
dev_sign_in = true

# Sign-in with GitHub: the client id and secret of an OAuth app whose callback URL is
# <public_url>/sign-in/github/callback. Left empty, GitHub's sign-in is off.
[github]
client_id = ""
client_secret = ""
"""


@dataclass(frozen=True)
class ServerConfig:
    """What a server needs to run: where its data is, where it listens, and how people sign in.

    `dev_sign_in` lets anyone sign in under any name, so it is refused, by `ValueError`, unless `host` is local.
    `secret_key` signs the session cookies.
    """

    data_folder: Path
    host: str
    port: int
    public_url: str
    dev_sign_in: bool
    github_client_id: str
    github_client_secret: str
    secret_key: str

    def __post_init__(self) -> None:
        if self.dev_sign_in and self.host not in _LOCAL_HOSTS:
            raise ValueError(f"dev_sign_in lets anyone in, so it needs a local host, not {self.host!r}")

    @property
    def database_url(self) -> str:
        """The SQLite database in the data folder."""
        return f"sqlite:///{self.data_folder / _DATABASE_FILE_NAME}"

    @property
    def github_sign_in(self) -> bool:
        """Whether people may sign in with GitHub, which needs a client id and secret."""
        return bool(self.github_client_id and self.github_client_secret)

    @classmethod
    def from_data_folder(cls, data_folder: Path) -> "ServerConfig":
        """The config in `data_folder`'s `server.toml`, with its secret key; either is written, with defaults, if
        missing, and the folder created."""
        data_folder.mkdir(parents=True, exist_ok=True)
        config_file = data_folder / CONFIG_FILE_NAME
        if not config_file.exists():
            config_file.write_text(_DEFAULT_CONFIG, encoding="utf-8")
            logger.info(f"Wrote the default config to {config_file}")
        settings: dict[str, Any] = tomllib.loads(config_file.read_text(encoding="utf-8"))
        github: dict[str, Any] = settings.get("github", {})
        return cls(
            data_folder=data_folder,
            host=settings.get("host", "127.0.0.1"),
            port=int(settings.get("port", 8000)),
            public_url=str(settings.get("public_url", "http://127.0.0.1:8000")).rstrip("/"),
            dev_sign_in=bool(settings.get("dev_sign_in", False)),
            github_client_id=github.get("client_id", ""),
            github_client_secret=github.get("client_secret", ""),
            secret_key=_secret_key_in(data_folder),
        )


def _secret_key_in(data_folder: Path) -> str:
    """The secret key kept in `data_folder`, made, and readable by this user alone, if there is none yet."""
    key_file = data_folder / _SECRET_KEY_FILE_NAME
    if not key_file.exists():
        descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as key:
            key.write(secrets.token_urlsafe(48))
    return key_file.read_text(encoding="utf-8").strip()
