from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from sc2scala.server.app import create_app
from sc2scala.server.github_accounts import GitHubUser
from sc2scala.server.models import User
from tests.conftest import FakeGitHub, csrf_of, server_config_in, sign_in_dev


def users_of(server: TestClient) -> list[tuple[str, int | None]]:
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        return [(user.login, user.github_id) for user in session.scalars(select(User).order_by(User.id))]


def sign_in_with_github(server: TestClient, code: str, *, next: str | None = None) -> str:
    """Signs in with GitHub's code `code`, and returns the path the server sends the browser to after."""
    started = server.get("/sign-in/github", params={"next": next} if next else None, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    returned = server.get("/sign-in/github/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert returned.status_code == 303, returned.text
    return returned.headers["location"]


def test_the_dev_sign_in_makes_a_user_and_signs_them_in(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    assert "<h1>alice</h1>" in server.get("/account").text
    assert users_of(server) == [("alice", None)]


def test_signing_in_again_finds_the_same_user(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    sign_in_dev(server, "alice")
    assert users_of(server) == [("alice", None)]


def test_a_name_github_could_not_give_is_refused(server: TestClient) -> None:
    response = server.post("/sign-in/dev", data={"login": "-alice", "csrf_token": csrf_of(server)})
    assert response.status_code == 400
    assert users_of(server) == []


def test_a_server_without_the_dev_sign_in_refuses_it(tmp_path: Path) -> None:
    with TestClient(create_app(server_config_in(tmp_path / "server", dev_sign_in=False))) as server:
        assert "Dev sign-in" not in server.get("/sign-in").text
        response = server.post("/sign-in/dev", data={"login": "alice", "csrf_token": "any"})
        assert response.status_code == 404


def test_a_form_without_the_sessions_token_is_refused(server: TestClient) -> None:
    csrf_of(server)
    response = server.post("/sign-in/dev", data={"login": "alice", "csrf_token": "forged"})
    assert response.status_code == 403
    assert users_of(server) == []


def test_github_makes_a_user_at_the_first_sign_in_and_renames_them_later(
    server: TestClient, fake_github: FakeGitHub
) -> None:
    fake_github.accounts_by_code["first"] = GitHubUser(id=42, login="alice")
    fake_github.accounts_by_code["second"] = GitHubUser(id=42, login="alice-renamed")
    assert sign_in_with_github(server, "first") == "/account"
    assert sign_in_with_github(server, "second") == "/account"
    assert users_of(server) == [("alice-renamed", 42)]
    assert "<h1>alice-renamed</h1>" in server.get("/account").text


def test_a_github_sign_in_not_started_here_is_refused(server: TestClient, fake_github: FakeGitHub) -> None:
    fake_github.accounts_by_code["code"] = GitHubUser(id=42, login="alice")
    server.get("/sign-in/github", follow_redirects=False)
    response = server.get("/sign-in/github/callback", params={"code": "code", "state": "guessed"})
    assert response.status_code == 400
    assert users_of(server) == []


def test_a_code_github_refuses_signs_nobody_in(server: TestClient) -> None:
    started = server.get("/sign-in/github", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    response = server.get("/sign-in/github/callback", params={"code": "unknown", "state": state})
    assert response.status_code == 400
    assert "bad_verification_code" in response.text


def test_a_github_login_another_user_holds_is_refused(server: TestClient, fake_github: FakeGitHub) -> None:
    sign_in_dev(server, "alice")
    fake_github.accounts_by_code["code"] = GitHubUser(id=42, login="alice")
    started = server.get("/sign-in/github", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    assert server.get("/sign-in/github/callback", params={"code": "code", "state": state}).status_code == 400
    assert users_of(server) == [("alice", None)]


def test_the_dev_sign_in_cannot_take_a_github_users_name(server: TestClient, fake_github: FakeGitHub) -> None:
    fake_github.accounts_by_code["code"] = GitHubUser(id=42, login="alice")
    sign_in_with_github(server, "code")
    server.cookies.clear()
    response = server.post("/sign-in/dev", data={"login": "alice", "csrf_token": csrf_of(server)})
    assert response.status_code == 400
    assert "signs in with GitHub" in response.text


def test_a_sign_in_returns_to_a_page_of_this_server_alone(server: TestClient, fake_github: FakeGitHub) -> None:
    fake_github.accounts_by_code["code"] = GitHubUser(id=42, login="alice")
    assert sign_in_with_github(server, "code", next="/account") == "/account"
    assert sign_in_with_github(server, "code", next="https://evil.example/") == "/account"
    assert sign_in_with_github(server, "code", next="//evil.example/") == "/account"


def test_a_page_for_the_signed_in_sends_others_to_sign_in(server: TestClient) -> None:
    response = server.get("/account", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in?next=%2Faccount"


def test_signing_out_ends_the_session(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    server.post("/sign-out", data={"csrf_token": csrf_of(server, "/account")})
    assert server.get("/account", follow_redirects=False).status_code == 303
