import io
import os
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc2scala.errors import SignInError
from sc2scala.server.app import create_app
from sc2scala.server.github_accounts import GitHubUser
from sc2scala.server.server_config import ServerConfig

_CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')


class FakeGitHub:
    """Stands in for GitHub's sign-in: a code names the account that signs in with it."""

    def __init__(self) -> None:
        self.accounts_by_code: dict[str, GitHubUser] = {}

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return f"https://github.test/authorize?state={state}"

    def user_for_code(self, code: str, *, redirect_uri: str) -> GitHubUser:
        if code not in self.accounts_by_code:
            raise SignInError("GitHub refused the sign-in: bad_verification_code")
        return self.accounts_by_code[code]


def server_config_in(data_folder: Path, *, dev_sign_in: bool = True) -> ServerConfig:
    data_folder.mkdir(parents=True, exist_ok=True)
    return ServerConfig(
        data_folder=data_folder,
        host="127.0.0.1",
        port=8000,
        public_url="http://localhost",
        dev_sign_in=dev_sign_in,
        github_client_id="",
        github_client_secret="",
        secret_key="a secret for the tests",
    )


@pytest.fixture(autouse=True)
def no_browser_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails a test that would open a browser on the machine running it, as `sc2scala login` does."""

    def refuse(url: str, *args: object, **options: object) -> bool:
        raise AssertionError(f"A test tried to open {url} in a browser")

    monkeypatch.setattr("webbrowser.open", refuse)


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def server(tmp_path: Path, fake_github: FakeGitHub) -> Iterator[TestClient]:
    """A server with the dev sign-in and the fake GitHub, on a database of its own, and a browser on it."""
    app = create_app(server_config_in(tmp_path / "server"), github_accounts=fake_github, tidies_storage=False)
    with TestClient(app, base_url="http://localhost") as client:
        yield client


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user's config, cache and data folders, each `tmp_path / <its variable, in lower case>`."""
    for variable in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    return tmp_path


def csrf_of(client: TestClient, page: str = "/sign-in") -> str:
    """The form token on `page` of the browser's session."""
    found = _CSRF_FIELD.search(client.get(page).text)
    assert found is not None, f"{page} holds no form"
    return found.group(1)


def sign_in_dev(client: TestClient, login: str) -> None:
    response = client.post("/sign-in/dev", data={"login": login, "csrf_token": csrf_of(client)})
    assert response.status_code == 200, response.text


@pytest.fixture
def map_file() -> Path:
    """The map named by `SC2SCALA_TEST_MAP`, a path to an `.SC2Map` file, for the tests that play a match."""
    test_map = os.environ.get("SC2SCALA_TEST_MAP")
    if not test_map:
        pytest.skip("SC2SCALA_TEST_MAP names no map")
    return Path(test_map)


_TOKEN_IN_PAGE = re.compile(r"<pre>(scala_[A-Za-z0-9_-]+)</pre>")
MAP_BYTES = b"MPQ\x1b" + b"a map" * 20


def api_headers_of(server: TestClient, login: str) -> dict[str, str]:
    """The headers with which `login`, signed in with the dev sign-in, calls the API, by a token of theirs."""
    sign_in_dev(server, login)
    page = server.post("/account/tokens", data={"name": "tests", "csrf_token": csrf_of(server, "/account")}).text
    found = _TOKEN_IN_PAGE.search(page)
    assert found is not None, page
    server.cookies.clear()
    return {"Authorization": f"Bearer {found.group(1)}"}


def zip_of(files: dict[str, bytes]) -> bytes:
    """A zip holding `files`, each name a path in it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


PYTHON_BOT_ZIP = zip_of({"run.py": b"print('a bot')", "bot.py": b"# the bot"})
