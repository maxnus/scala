"""Adding a runner by a setup code: the page that makes one, its exchange for a machine, and a machine removing
itself, by the API and the command line."""

import json
import random
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from sc2scala.cli import main
from sc2scala.runner_commands import machine_name_for_host
from sc2scala.server.models import MatchRecord, RunnerSetupCode, utc_now
from sc2scala.server.runner_setup import MOST_SETUP_CODES_WAITING
from tests.conftest import csrf_of, sign_in_dev
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots
from tests.test_ladders_api import create_ladder

_SETUP_CODE_IN_PAGE = re.compile(r"--setup-code (scala_setup_[A-Za-z0-9_-]+)")
_NOTICE = re.compile(r'<p class="notice[^"]*"[^>]*>([^<]*)</p>')


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(5)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def add_runner_form(server: TestClient, login: str, **fields: Any) -> Any:
    """The page that answers `login`'s sending the Add a runner form with `fields`, signed in, and signed out again
    after: the code, or the form again with why it was refused."""
    sign_in_dev(server, login)
    response = server.post("/runners/add", data={"csrf_token": csrf_of(server, "/runners/add"), **fields})
    server.cookies.clear()
    return response


def setup_code_of(server: TestClient, login: str, *, serves_public: bool = True, ladders: tuple[str, ...] = ()) -> str:
    """A setup code `login` makes on the Add a runner page."""
    fields = {"ladders": list(ladders)} | ({"serves_public": "true"} if serves_public else {})
    page = add_runner_form(server, login, **fields).text
    found = _SETUP_CODE_IN_PAGE.search(page)
    assert found is not None, page
    return found.group(1)


def exchange(server: TestClient, code: str, name: str = "study", *, replace: bool = False) -> Any:
    return server.post("/api/runners/setup", json={"setup_code": code, "name": name, "replace": replace})


def machines_of(server: TestClient, headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {machine["name"]: machine for machine in server.get("/api/runners", headers=headers).json()}


def notice_of(page: str) -> str:
    found = _NOTICE.search(page)
    assert found is not None, page
    return found.group(1)


def test_the_add_a_runner_page_asks_for_a_sign_in(server: TestClient) -> None:
    response = server.get("/runners/add", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/sign-in")


def test_from_a_ladders_page_the_form_ticks_that_ladder_alone(ladder: LadderWithBots) -> None:
    server = ladder.server
    sign_in_dev(server, "alice")
    assert 'href="/runners/add?ladder=league">add a runner</a>' in server.get("/ladders/league").text
    assert 'href="/runners/add">Add a runner</a>' in server.get("/account").text
    from_ladder = server.get("/runners/add?ladder=league").text
    assert 'name="serves_public" value="true">' in from_ladder
    assert 'name="ladders" value="league" checked>' in from_ladder
    from_account = server.get("/runners/add").text
    assert 'name="serves_public" value="true" checked>' in from_account
    assert 'name="ladders" value="league">' in from_account


def test_a_setup_code_registers_one_machine_serving_what_it_says(ladder: LadderWithBots) -> None:
    server = ladder.server
    page = add_runner_form(server, "alice", ladders=["league"]).text
    assert "serving\n        league." in page
    assert "--server http://localhost --setup-code scala_setup_" in page
    code = _SETUP_CODE_IN_PAGE.search(page).group(1)  # type: ignore[union-attr]
    registered = exchange(server, code)
    assert registered.status_code == 200, registered.text
    assert registered.json()["token"].startswith("scala_runner_")
    assert {key: registered.json()[key] for key in ("name", "owner", "serves_public", "ladders")} == {
        "name": "study",
        "owner": "alice",
        "serves_public": False,
        "ladders": ["league"],
    }
    assert exchange(server, code, "attic").status_code == 404, "a code works once"
    assert set(machines_of(server, ladder.alice)) == {"desk", "study"}
    machine = {"Authorization": f"Bearer {registered.json()['token']}"}
    assert draw(server, machine) is not None


def test_an_expired_setup_code_registers_nothing(ladder: LadderWithBots) -> None:
    server = ladder.server
    code = setup_code_of(server, "alice")
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        session.execute(update(RunnerSetupCode).values(expires_at=utc_now() - timedelta(seconds=1)))
        session.commit()
    response = exchange(server, code)
    assert response.status_code == 404
    assert "used already, or expired" in response.json()["detail"]
    assert exchange(server, "scala_setup_never-made").status_code == 404


def test_a_name_taken_needs_replace_which_voids_the_old_machines_matches(ladder: LadderWithBots) -> None:
    server = ladder.server
    leased = draw(server, ladder.runner)
    assert leased is not None
    code = setup_code_of(server, "alice")
    taken = exchange(server, code, "desk")
    assert taken.status_code == 409
    assert "alice has a machine named desk already" in taken.json()["detail"]
    assert exchange(server, code, "Bad Name").status_code == 422
    replaced = exchange(server, code, "desk", replace=True)
    assert replaced.status_code == 200, "a refusal leaves the code unused"
    assert server.post("/api/runner/matches", headers=ladder.runner).status_code == 401
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        match = session.get(MatchRecord, leased["id"])
        assert match is not None
        assert (match.status, match.void_because) == ("void", "its runner machine was set up again")
    assert machines_of(server, ladder.alice)["desk"]["serves_public"] is True


def test_a_setup_code_holds_only_ladders_one_may_serve(ladder: LadderWithBots) -> None:
    server = ladder.server
    create_ladder(server, ladder.bob, "hideout", password="sesame")
    refused = add_runner_form(server, "alice", ladders=["hideout"])
    assert notice_of(refused.text) == "hideout is protected: join it with its password first"
    nothing = add_runner_form(server, "alice")
    assert notice_of(nothing.text) == "A runner serves every public ladder, or the ladders ticked: tick one at least"


def test_a_user_holds_ten_unused_setup_codes_at_most(ladder: LadderWithBots) -> None:
    server = ladder.server
    for _ in range(MOST_SETUP_CODES_WAITING):
        setup_code_of(server, "alice")
    refused = add_runner_form(server, "alice", serves_public="true")
    assert notice_of(refused.text).startswith(f"You hold {MOST_SETUP_CODES_WAITING} unused setup codes")
    setup_code_of(server, "bob")


def test_a_machine_removes_itself_by_its_token(ladder: LadderWithBots) -> None:
    server = ladder.server
    leased = draw(server, ladder.runner)
    assert leased is not None
    removed = server.delete("/api/runner/machine", headers=ladder.runner)
    assert removed.json() == {"removed": "alice/desk"}
    assert server.delete("/api/runner/machine", headers=ladder.runner).status_code == 401
    assert machines_of(server, ladder.alice) == {}
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        match = session.get(MatchRecord, leased["id"])
        assert match is not None
        assert (match.status, match.void_because) == ("void", "its runner machine was removed")


@pytest.mark.parametrize(
    ("host_name", "machine_name"),
    [
        ("max-9950", "max-9950"),
        ("Max_Desk.fritz.box", "Max-Desk"),
        ("--DESKTOP--", "DESKTOP"),
        ("ünïcode", "n-code"),
        ("a" * 50, "a" * 40),
        ("___", "runner"),
    ],
)
def test_a_host_name_is_made_a_machines_name(host_name: str, machine_name: str) -> None:
    assert machine_name_for_host(host_name) == machine_name


def test_runner_register_by_a_setup_code_needs_no_sign_in(
    ladder: LadderWithBots, folders: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = ladder.server
    monkeypatch.setattr("socket.gethostname", lambda: "Max_Desk.fritz.box")
    code = setup_code_of(server, "alice", ladders=("league",))
    assert main(["runner", "register", "--setup-code", code], http=server) == 1
    assert "Name the server the setup code is from with --server" in capsys.readouterr().err
    with_ladders = ["runner", "register", "--server", "http://localhost", "--setup-code", code, "--ladders", "league"]
    assert main(with_ladders, http=server) == 1
    assert main(["runner", "register", "--server", "http://localhost", "--setup-code", code], http=server) == 0
    assert "This computer is alice/Max-Desk on http://localhost" in capsys.readouterr().out
    runner_file = folders / "xdg_config_home" / "sc2scala" / "runner.json"
    assert json.loads(runner_file.read_text(encoding="utf-8"))["machine"] == "Max-Desk"
    assert (folders / "xdg_config_home" / "sc2scala" / "runner.toml").exists()
    again = setup_code_of(server, "alice")
    assert main(["runner", "register", "--server", "http://localhost", "--setup-code", again], http=server) == 1
    assert "alice has a machine named Max-Desk already" in capsys.readouterr().err
    replace = ["runner", "register", "--server", "http://localhost", "--setup-code", again, "--replace"]
    assert main(replace, http=server) == 0
    assert machines_of(server, ladder.alice)["Max-Desk"]["serves_public"] is True


def test_runner_remove_with_no_name_removes_this_computer(
    ladder: LadderWithBots, folders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = ladder.server
    code = setup_code_of(server, "alice")
    assert main(["runner", "register", "attic", "--server", "http://localhost", "--setup-code", code], http=server) == 0
    capsys.readouterr()
    assert main(["runner", "remove"], http=server) == 0
    assert capsys.readouterr().out == (
        "Removed alice/attic from http://localhost; the matches it was playing are void\n"
    )
    assert "attic" not in machines_of(server, ladder.alice)
    assert not (folders / "xdg_config_home" / "sc2scala" / "runner.json").exists()
    assert main(["runner", "remove"], http=server) == 1
    assert "This computer is no runner machine yet" in capsys.readouterr().err
