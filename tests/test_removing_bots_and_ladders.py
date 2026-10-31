"""Removing a bot or a ladder: deleted if it never played, or else retired, its matches kept."""

import io
import random
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sc2scala.bot_commands import removal_line
from tests.conftest import PYTHON_BOT_ZIP, sign_in_dev, zip_of
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, machine_headers, report
from tests.test_bot_forms import submit
from tests.test_bots_api import upload
from tests.test_ladders_api import create_ladder


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(11)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def play_one(server: TestClient, runner: dict[str, str]) -> dict[str, Any]:
    match = draw(server, runner)
    assert match is not None
    assert report(server, runner, match["id"], "Player1Win", with_files=False).status_code == 200
    return match


def entries_of(ladder: LadderWithBots, name: str = "league") -> list[str]:
    details = ladder.server.get(f"/api/ladders/{name}", headers=ladder.alice).json()
    return [entry["bot"] for entry in details["entries"]]


def test_a_bot_that_never_played_is_deleted_and_its_name_freed(ladder: LadderWithBots) -> None:
    server = ladder.server
    upload(server, ladder.alice, "spare_bot", PYTHON_BOT_ZIP)
    server.post("/api/ladders/league/entries", headers=ladder.alice, json={"bot": "spare_bot"})
    assert server.delete("/api/bots/spare_bot", headers=ladder.bob).status_code == 403
    removed = server.delete("/api/bots/spare_bot", headers=ladder.alice).json()
    assert removed == {"name": "spare_bot", "outcome": "deleted", "matches": 0}
    assert server.get("/api/bots/spare_bot", headers=ladder.alice).status_code == 404
    assert "spare_bot" not in entries_of(ladder)
    assert upload(server, ladder.bob, "spare_bot", zip_of({"run.py": b"bob's"}))["status"] == 200


def test_a_bot_that_played_is_retired_out_of_every_open_ladder_and_keeps_its_matches(ladder: LadderWithBots) -> None:
    server = ladder.server
    create_ladder(server, ladder.alice, "old-cup")
    for headers, bot in ((ladder.alice, "basic_bot"), (ladder.bob, "bobs_bot")):
        server.post("/api/ladders/old-cup/entries", headers=headers, json={"bot": bot})
    cup_runner = machine_headers(server, ladder.alice, "cup-desk", serves_public=False, ladders=["old-cup"])
    cup_match = play_one(server, cup_runner)
    league_match = play_one(server, ladder.runner)
    assert server.delete("/api/ladders/old-cup", headers=ladder.alice).json()["outcome"] == "retired"

    removed = server.delete("/api/bots/basic_bot", headers=ladder.alice).json()
    assert removed == {"name": "basic_bot", "outcome": "retired", "matches": 2}
    assert entries_of(ladder) == ["bobs_bot"]
    assert entries_of(ladder, "old-cup") == ["basic_bot", "bobs_bot"], "a retired ladder's standings stay as they ended"
    assert server.get("/api/bots/basic_bot", headers=ladder.alice).json()["retired_at"] is not None
    for match in (cup_match, league_match):
        assert server.get(f"/api/matches/{match['id']}", headers=ladder.bob).status_code == 200
    refused = [
        upload(server, ladder.alice, "basic_bot", zip_of({"run.py": b"new"}))["status"],
        server.post("/api/ladders/league/entries", headers=ladder.alice, json={"bot": "basic_bot"}).status_code,
        server.delete("/api/bots/basic_bot", headers=ladder.alice).status_code,
    ]
    assert refused == [409, 409, 409]
    bots_page = server.get("/bots").text
    assert '<a href="/bots/bobs_bot">bobs_bot</a>' in bots_page and "basic_bot" not in bots_page
    bot_page = server.get("/bots/basic_bot").text
    assert "Retired " in bot_page
    assert f'<a href="/matches/{league_match["id"]}">' in bot_page, "a retired bot's page keeps its matches"


def test_a_ladder_that_never_drew_is_deleted_and_its_name_freed(ladder: LadderWithBots) -> None:
    server = ladder.server
    create_ladder(server, ladder.alice, "night")
    server.post("/api/ladders/night/entries", headers=ladder.bob, json={"bot": "bobs_bot"})
    machine_headers(server, ladder.bob, "bobs-desk", serves_public=False, ladders=["night"])
    assert server.delete("/api/ladders/night", headers=ladder.bob).status_code == 403
    removed = server.delete("/api/ladders/night", headers=ladder.alice).json()
    assert removed == {"name": "night", "outcome": "deleted", "matches": 0}
    assert server.get("/api/ladders/night", headers=ladder.alice).status_code == 404
    assert server.get("/api/runners", headers=ladder.bob).json()[0]["ladders"] == []
    assert create_ladder(server, ladder.bob, "night").status_code == 200


def test_a_ladder_that_drew_is_retired_draws_no_more_and_keeps_its_standings(ladder: LadderWithBots) -> None:
    server = ladder.server
    machine_headers(server, ladder.bob, "bobs-desk", serves_public=False, ladders=["league"])
    played = play_one(server, ladder.runner)
    removed = server.delete("/api/ladders/league", headers=ladder.alice).json()
    assert removed == {"name": "league", "outcome": "retired", "matches": 1}
    assert server.get("/api/ladders", headers=ladder.alice).json() == []
    assert server.get("/api/ladders/league", headers=ladder.alice).json()["retired_at"] is not None
    assert len(server.get("/api/ladders/league/standings", headers=ladder.alice).json()) == 2
    assert server.get(f"/api/matches/{played['id']}", headers=ladder.bob).status_code == 200
    assert draw(server, ladder.runner) is None
    assert server.get("/api/runners", headers=ladder.bob).json()[0]["ladders"] == []
    upload(server, ladder.bob, "second_bot", zip_of({"run.py": b"second"}))
    entered = server.post("/api/ladders/league/entries", headers=ladder.bob, json={"bot": "second_bot"})
    assert (entered.status_code, entered.json()["detail"]) == (409, "league is retired: it takes no new entry")
    assert server.delete("/api/ladders/league", headers=ladder.alice).status_code == 409
    assert "/ladders/league" not in server.get("/").text
    assert "Retired " in server.get("/ladders/league").text


def test_the_pages_delete_a_bot_that_never_played_and_retire_one_that_did(ladder: LadderWithBots) -> None:
    server = ladder.server
    upload(server, ladder.alice, "spare_bot", PYTHON_BOT_ZIP)
    play_one(server, ladder.runner)
    sign_in_dev(server, "bob")
    assert "Delete basic_bot" not in server.get("/bots/basic_bot").text
    sign_in_dev(server, "alice")
    assert "It never played, so it is deleted" in server.get("/bots/spare_bot").text
    page = submit(server, "/bots/spare_bot", "/bots/spare_bot/remove", {})
    assert str(page.url).endswith("/account") and "Deleted spare_bot" in page.text
    assert "It played 1 match, so it is retired" in server.get("/bots/basic_bot").text
    page = submit(server, "/bots/basic_bot", "/bots/basic_bot/remove", {})
    assert str(page.url).endswith("/bots/basic_bot") and "Retired basic_bot: it plays no more" in page.text
    assert "Upload a new version" not in page.text and "Retire basic_bot…" not in page.text
    assert 'basic_bot</a> <span class="muted">retired</span>' in server.get("/account").text


def test_the_settings_page_deletes_a_ladder_that_never_drew_and_retires_one_that_did(ladder: LadderWithBots) -> None:
    server = ladder.server
    create_ladder(server, ladder.alice, "spare-ladder")
    play_one(server, ladder.runner)
    sign_in_dev(server, "alice")
    assert "It never drew a match, so it is deleted" in server.get("/ladders/spare-ladder/settings").text
    page = submit(server, "/ladders/spare-ladder/settings", "/ladders/spare-ladder/remove", {})
    assert str(page.url).endswith("/") and "Deleted spare-ladder" in page.text
    assert "It drew 1 match, so it is retired" in server.get("/ladders/league/settings").text
    page = submit(server, "/ladders/league/settings", "/ladders/league/remove", {})
    assert str(page.url).endswith("/ladders/league") and "Retired league: it draws no more matches" in page.text
    assert "Enter it" not in page.text
    assert "Retired " in server.get("/ladders/league/settings").text


def test_what_a_removal_did_is_said_in_a_line() -> None:
    assert removal_line({"name": "night", "outcome": "deleted", "matches": 0}, played="drew", stays="") == (
        "Deleted night"
    )
    retired = {"name": "basic_bot", "outcome": "retired", "matches": 1}
    assert removal_line(retired, played="played", stays="it plays no more") == (
        "Retired basic_bot, which played 1 match: it plays no more"
    )


def test_removing_asks_at_the_terminal_and_needs_yes_without_one(
    ladder: LadderWithBots, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from sc2scala.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    token = ladder.alice["Authorization"].removeprefix("Bearer ")
    assert main(["login", "http://localhost", "--token", token], http=ladder.server) == 0
    upload(ladder.server, ladder.alice, "spare_bot", PYTHON_BOT_ZIP)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["bot", "remove", "spare_bot"], http=ladder.server) == 1
    terminal = io.StringIO("")
    terminal.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", terminal)
    monkeypatch.setattr("builtins.input", lambda question: "n")
    assert main(["bot", "remove", "spare_bot"], http=ladder.server) == 1
    monkeypatch.setattr("builtins.input", lambda question: "y")
    assert main(["bot", "remove", "spare_bot"], http=ladder.server) == 0
    assert ladder.server.get("/api/bots/spare_bot", headers=ladder.alice).status_code == 404
    create_ladder(ladder.server, ladder.alice, "night")
    assert main(["ladder", "remove", "night", "--yes"], http=ladder.server) == 0
    assert ladder.server.get("/api/ladders/night", headers=ladder.alice).status_code == 404
