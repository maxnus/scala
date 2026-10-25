import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc2scala.cli import main
from tests.conftest import sign_in_dev
from tests.test_api_tokens import make_token


@pytest.fixture
def credentials_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "sc2scala" / "credentials.json"


@pytest.fixture
def token(server: TestClient) -> str:
    sign_in_dev(server, "alice")
    made = make_token(server)
    server.cookies.clear()
    return made


def test_login_keeps_the_token_for_this_user_alone(
    server: TestClient, token: str, credentials_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["login", "http://localhost/", "--token", token], http=server) == 0
    assert "Signed in to http://localhost as alice" in capsys.readouterr().out
    assert json.loads(credentials_file.read_text(encoding="utf-8")) == {
        "default_server": "http://localhost",
        "tokens": {"http://localhost": token},
    }
    assert stat.S_IMODE(credentials_file.stat().st_mode) == 0o600


def test_an_empty_token_is_refused_with_where_to_make_one(
    server: TestClient, credentials_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["login", "http://localhost", "--token", " "], http=server) == 1
    assert "The token is empty: make one on http://localhost/account" in capsys.readouterr().err
    assert not credentials_file.exists()


def test_a_token_the_server_refuses_is_not_kept(server: TestClient, credentials_file: Path) -> None:
    assert main(["login", "http://localhost", "--token", "scala_wrong"], http=server) == 1
    assert not credentials_file.exists()


def test_whoami_asks_the_server(
    server: TestClient, token: str, credentials_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["login", "http://localhost", "--token", token], http=server)
    assert main(["whoami"], http=server) == 0
    assert "alice on http://localhost" in capsys.readouterr().out


def test_whoami_before_any_login_fails(server: TestClient, credentials_file: Path) -> None:
    assert main(["whoami"], http=server) == 1


def test_logout_forgets_the_token(server: TestClient, token: str, credentials_file: Path) -> None:
    main(["login", "http://localhost", "--token", token], http=server)
    assert main(["logout"]) == 0
    assert json.loads(credentials_file.read_text(encoding="utf-8")) == {"default_server": None, "tokens": {}}
    assert main(["whoami"], http=server) == 1
