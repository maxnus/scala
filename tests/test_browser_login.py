"""`sc2scala login` through the browser: the command's request, and the page that confirms it."""

import io
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from sc2scala.api_tokens import hash_of_token, new_token
from sc2scala.cli import main
from sc2scala.errors import ServerError
from sc2scala.scala_client import ScalaClient
from sc2scala.server.models import LoginRequest
from tests.conftest import csrf_of, sign_in_dev
from tests.test_api_tokens import make_token


@pytest.fixture
def credentials_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "sc2scala" / "credentials.json"


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs the command as from a terminal, where it signs in through the browser rather than read stdin."""
    monkeypatch.setattr("sc2scala.account_commands._stdin_is_terminal", lambda: True)


class BrowserAnswering:
    """Stands in for the browser `sc2scala login` opens, and for the person at it: signed in as `login`, they answer
    the code with `answer` while the command waits."""

    def __init__(self, server: TestClient, login: str, answer: str) -> None:
        self.server = server
        self.login = login
        self.answer = answer
        self.opened: list[str] = []
        self.answered = False

    def open(self, url: str, *args: object, **options: object) -> bool:
        self.opened.append(url)
        return True

    def wait(self, seconds: float) -> None:
        """The command's wait between asks, in which the person answers, once."""
        if self.answered:
            raise AssertionError("The command kept waiting after the code was answered")
        self.answered = True
        browser = TestClient(self.server.app, base_url="http://localhost")
        sign_in_dev(browser, self.login)
        [url] = self.opened
        page = browser.get(url.removeprefix("http://localhost"))
        assert "Confirm only if your terminal shows this same code" in page.text
        code = url.rsplit("=", 1)[1]
        browser.post("/login", data={"code": code, "answer": self.answer, "csrf_token": csrf_of(browser, "/account")})


def run_login(server: TestClient, monkeypatch: pytest.MonkeyPatch, *, answer: str) -> tuple[int, BrowserAnswering]:
    browser = BrowserAnswering(server, "alice", answer)
    monkeypatch.setattr("webbrowser.open", browser.open)
    monkeypatch.setattr("sc2scala.account_commands.time.sleep", browser.wait)
    return main(["login", "http://localhost"], http=server), browser


def test_the_command_is_signed_in_once_the_code_is_confirmed_in_the_browser(
    server: TestClient,
    credentials_file: Path,
    at_a_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, browser = run_login(server, monkeypatch, answer="confirm")
    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"on {browser.opened[0]}" in out and "Signed in to http://localhost as alice" in out
    sign_in_dev(server, "alice")
    assert "sc2scala on " in server.get("/account").text


def test_a_refused_sign_in_ends_the_command_at_once(
    server: TestClient,
    credentials_file: Path,
    at_a_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, _ = run_login(server, monkeypatch, answer="refuse")
    assert exit_code == 1
    assert "was refused, or expired" in capsys.readouterr().err
    assert not credentials_file.exists()


def test_without_a_browser_the_command_shows_the_page_to_open(
    server: TestClient,
    credentials_file: Path,
    at_a_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser = BrowserAnswering(server, "alice", "confirm")
    monkeypatch.setattr("sc2scala.account_commands.time.sleep", browser.wait)
    printed: list[str] = []

    def note_printed_page(*args: Any, **options: Any) -> None:
        text = " ".join(str(arg) for arg in args)
        printed.append(text)
        if "Confirm the code" in text:
            browser.opened.append(text.rsplit(" ", 1)[1])

    monkeypatch.setattr("builtins.print", note_printed_page)
    assert main(["login", "http://localhost", "--no-browser"], http=server) == 0
    assert printed[0].startswith("Confirm the code ") and "/login?code=" in printed[0]


def request_login(server: TestClient, token_hash: str | None = None) -> Any:
    return server.post(
        "/api/login-requests", json={"token_hash": token_hash or hash_of_token(new_token()), "name": "x"}
    )


def test_the_confirm_page_signs_in_first_and_takes_the_code_as_typed(server: TestClient) -> None:
    code = request_login(server).json()["code"]
    first = server.get(f"/login?code={code}", follow_redirects=False)
    assert (first.status_code, first.headers["location"]) == (303, f"/sign-in?next=%2Flogin%3Fcode%3D{code}")
    sign_in_dev(server, "alice")
    typed = code.replace("-", "").lower()
    assert f'<p class="code-to-compare">{code}</p>' in server.get(f"/login?code={typed}").text


def test_an_expired_code_is_answered_no_more(server: TestClient) -> None:
    code = request_login(server).json()["code"]
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        session.execute(update(LoginRequest).values(expires_at=datetime(2026, 1, 1).astimezone()))
        session.commit()
    assert server.get(f"/api/login-requests/{code}").status_code == 404
    sign_in_dev(server, "alice")
    assert "it expired, or was answered already" in server.get(f"/login?code={code}").text
    answered = server.post(
        "/login", data={"code": code, "answer": "confirm", "csrf_token": csrf_of(server, "/account")}
    )
    assert "No command waits with the code" in answered.text


def test_a_request_needs_a_hash_and_one_unknown(server: TestClient) -> None:
    assert request_login(server, "not a hash").status_code == 422
    token_hash = hash_of_token(new_token())
    assert request_login(server, token_hash).status_code == 200
    assert request_login(server, token_hash).status_code == 409


def test_a_server_out_of_reach_a_moment_does_not_end_the_wait(
    server: TestClient, credentials_file: Path, at_a_terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    asks = 0
    really_waits = ScalaClient.login_request_waits

    def out_of_reach_once(client: ScalaClient, code: str) -> bool:
        nonlocal asks
        asks += 1
        if asks == 1:
            raise ServerError("http://localhost could not be reached: connection reset")
        return really_waits(client, code)

    monkeypatch.setattr(ScalaClient, "login_request_waits", out_of_reach_once)
    exit_code, _ = run_login(server, monkeypatch, answer="confirm")
    assert (exit_code, asks) == (0, 1)


def test_ctrl_c_stops_the_wait_with_a_word_not_a_traceback(
    server: TestClient,
    credentials_file: Path,
    at_a_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("webbrowser.open", lambda url, *args, **options: True)
    monkeypatch.setattr("sc2scala.account_commands.time.sleep", interrupt)
    assert main(["login", "http://localhost"], http=server) == 1
    assert "Stopped waiting; the code" in capsys.readouterr().err


def test_a_token_piped_on_stdin_signs_in_without_the_browser(
    server: TestClient, credentials_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sign_in_dev(server, "alice")
    token = make_token(server)
    server.cookies.clear()
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{token}\n"))
    assert main(["login", "http://localhost"], http=server) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["login", "http://localhost"], http=server) == 1


def test_a_request_is_named_in_plain_words_and_capped_by_address(server: TestClient) -> None:
    for name in ("Scala says: confirm\nnow", "x" * 61):
        refused = server.post("/api/login-requests", json={"token_hash": hash_of_token(new_token()), "name": name})
        assert refused.status_code == 422
    for _ in range(5):
        assert request_login(server).status_code == 200
    assert request_login(server).status_code == 429


def test_no_page_may_be_framed_by_another_site(server: TestClient) -> None:
    for page in ("/", "/sign-in", "/login"):
        response = server.get(page)
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_a_server_without_sign_in_through_the_browser_is_explained(
    credentials_file: Path, at_a_terminal: None, capsys: pytest.CaptureFixture[str]
) -> None:
    older_server = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={})))
    assert main(["login", "http://localhost", "--no-browser"], http=older_server) == 1
    error = capsys.readouterr().err
    assert "has no sign-in through the browser" in error
    assert "sc2scala login http://localhost --token TOKEN" in error
