import os
import re
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


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def server(tmp_path: Path, fake_github: FakeGitHub) -> Iterator[TestClient]:
    """A server with the dev sign-in and the fake GitHub, on a database of its own, and a browser on it."""
    app = create_app(server_config_in(tmp_path / "server"), github_accounts=fake_github)
    with TestClient(app, base_url="http://localhost") as client:
        yield client


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
