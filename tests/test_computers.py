"""The game's built-in computer as a ladder's player: added by its owner, drawn against its bots by runners whose
engine plays it, rated as a bot is, and retired once it played."""

import random
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sc2scala import Race
from sc2scala.cli import main
from sc2scala.computer import Build, Computer, Difficulty
from sc2scala.server.match_draw import _weight_of_ladder
from sc2scala.server.models import LadderEntry, MatchRecord
from tests.conftest import PYTHON_BOT_ZIP, api_headers_of, sign_in_dev
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, report
from tests.test_bot_forms import submit
from tests.test_bots_api import upload
from tests.test_runner import FakeEngine, runner_of

_ZERG = "Computer-VeryHard-Zerg-Rush"


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(11)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def add_computer(ladder: LadderWithBots, *, race: str = "Z", difficulty: str = "VeryHard", build: str = "Rush") -> Any:
    body = {"race": race, "difficulty": difficulty, "build": build}
    return ladder.server.post("/api/ladders/league/computers", headers=ladder.alice, json=body)


def withdraw_bobs_bot(ladder: LadderWithBots) -> None:
    assert ladder.server.delete("/api/ladders/league/entries/bobs_bot", headers=ladder.bob).status_code == 200


def standings(ladder: LadderWithBots) -> list[dict[str, Any]]:
    return ladder.server.get("/api/ladders/league/standings", headers=ladder.alice).json()


def test_a_computer_is_named_for_its_race_difficulty_and_build_and_read_back_from_its_name() -> None:
    computer = Computer(Race.RANDOM, Difficulty.CHEAT_INSANE)
    assert computer.name == "Computer-CheatInsane-Random-RandomBuild"
    assert Computer.from_name("computer-veryhard-zerg-rush") == Computer(Race.ZERG, Difficulty.VERY_HARD, Build.RUSH)
    for not_a_computer in ("Computer-Hard-Zerg", "Bot-Hard-Zerg-Rush", "Computer-Hardest-Zerg-Rush"):
        with pytest.raises(ValueError):
            Computer.from_name(not_a_computer)


def test_the_owner_adds_a_computer_once_and_nobody_else_may(ladder: LadderWithBots) -> None:
    added = add_computer(ladder)
    assert added.status_code == 200, added.text
    assert added.json()["computers"] == [
        {"computer": _ZERG, "race": "Z", "difficulty": "VeryHard", "build": "Rush", "paused": False}
    ]
    assert added.json()["computers"] and add_computer(ladder).status_code == 409
    body = {"race": "Zerg", "difficulty": "Easy"}
    assert ladder.server.post("/api/ladders/league/computers", headers=ladder.bob, json=body).status_code == 403
    assert add_computer(ladder, difficulty="Hardest").status_code == 422
    listed = ladder.server.get("/api/ladders", headers=ladder.bob).json()
    assert [(summary["bots"], summary["computers"]) for summary in listed] == [(2, 1)]


def test_a_runner_on_scala_s_engine_draws_the_computer_into_seat_2_and_no_other_runner_does(
    ladder: LadderWithBots,
) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    assert draw(ladder.server, ladder.runner) is None
    assert draw(ladder.server, ladder.runner, engine="aiarena") is None
    match = draw(ladder.server, ladder.runner, engine="scala")
    assert match is not None
    assert match["players"] == [
        {
            "seat": 1,
            "bot": "basic_bot",
            "race": "T",
            "type": "python",
            "version": 1,
            "sha256": match["players"][0]["sha256"],
        },
        {"seat": 2, "computer": _ZERG, "race": "Z", "difficulty": "VeryHard", "build": "Rush"},
    ]


def test_two_computers_never_play_each_other_and_a_paused_one_not_at_all(ladder: LadderWithBots) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    add_computer(ladder, race="P", difficulty="Easy", build="RandomBuild")
    drawn = [draw(ladder.server, ladder.runner, engine="scala") for _ in range(30)]
    assert all(match and match["players"][0]["bot"] == "basic_bot" for match in drawn)
    assert {match["players"][1]["computer"] for match in drawn if match} == {_ZERG, "Computer-Easy-Protoss-RandomBuild"}
    assert ladder.server.post(f"/api/ladders/league/computers/{_ZERG}/pause", headers=ladder.alice).status_code == 200
    drawn = [draw(ladder.server, ladder.runner, engine="scala") for _ in range(10)]
    assert {match["players"][1]["computer"] for match in drawn if match} == {"Computer-Easy-Protoss-RandomBuild"}
    ladder.server.post("/api/ladders/league/computers/Computer-Easy-Protoss-RandomBuild/pause", headers=ladder.alice)
    assert draw(ladder.server, ladder.runner, engine="scala") is None


def test_pairs_are_drawn_uniformly_among_bots_and_computers_alike(ladder: LadderWithBots) -> None:
    add_computer(ladder)
    drawn = [draw(ladder.server, ladder.runner, engine="scala") for _ in range(300)]
    pairs = Counter("computer" if "computer" in match["players"][1] else "bots" for match in drawn if match is not None)
    # Two bots and a computer make three pairs, two of them with the computer.
    assert 0.55 < pairs["computer"] / 300 < 0.78


def test_a_ladder_weighs_its_bots_over_the_bots_its_matches_seat() -> None:
    assert _weight_of_ladder(2, 6) == pytest.approx(2 / (14 / 13))
    assert _weight_of_ladder(4, 0) == 2
    assert _weight_of_ladder(1, 3) == 1


def test_a_computer_is_rated_as_a_bot_is_and_has_no_logs(ladder: LadderWithBots) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    match = draw(ladder.server, ladder.runner, engine="scala")
    assert match is not None
    assert report(ladder.server, ladder.runner, match["id"], "Player2Win").status_code == 200
    rows = {row.get("bot") or row["computer"]: row for row in standings(ladder)}
    assert (rows[_ZERG]["wins"], rows["basic_bot"]["losses"]) == (1, 1)
    assert rows[_ZERG]["rating"] > rows["basic_bot"]["rating"]
    played = ladder.server.get(f"/api/matches/{match['id']}", headers=ladder.alice).json()
    assert played["players"][1] == {"computer": _ZERG, "difficulty": "VeryHard", "build": "Rush", "race": "Z"}
    assert ladder.server.get(f"/api/matches/{match['id']}/logs/2", headers=ladder.alice).status_code == 404
    assert ladder.server.get(f"/api/matches/{match['id']}/logs/1", headers=ladder.alice).status_code == 200
    with Session(ladder.server.app.state.engine) as session:  # type: ignore[attr-defined]
        record = session.get(MatchRecord, match["id"])
        assert record is not None and record.player_2_logs_sha256 is None and record.player_1_logs_sha256
    pairs = ladder.server.get("/ladders/league/pairs")
    assert f"<th>{_ZERG}</th>" in pairs.text and "0–1–0" in pairs.text


def test_a_failed_start_against_the_computer_counts_for_the_bot(ladder: LadderWithBots) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    match = draw(ladder.server, ladder.runner, engine="scala")
    assert match is not None
    report(ladder.server, ladder.runner, match["id"], "InitializationError")
    with Session(ladder.server.app.state.engine) as session:  # type: ignore[attr-defined]
        assert [entry.failed_starts_in_a_row for entry in session.query(LadderEntry)] == [1]
    assert {row.get("bot") or row["computer"]: row["games"] for row in standings(ladder)} == {"basic_bot": 0, _ZERG: 0}


def test_a_computer_that_played_is_retired_and_added_again_keeps_its_record(ladder: LadderWithBots) -> None:
    add_computer(ladder, race="T", difficulty="Easy", build="Air")
    removed = ladder.server.delete("/api/ladders/league/computers/Computer-Easy-Terran-Air", headers=ladder.alice)
    assert removed.json() == {"name": "Computer-Easy-Terran-Air", "outcome": "deleted", "matches": 0}
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    match = draw(ladder.server, ladder.runner, engine="scala")
    assert match is not None
    report(ladder.server, ladder.runner, match["id"], "Player1Win")
    removed = ladder.server.delete(f"/api/ladders/league/computers/{_ZERG}", headers=ladder.alice)
    assert removed.json() == {"name": _ZERG, "outcome": "retired", "matches": 1}
    assert [row.get("computer") for row in standings(ladder)] == [None]
    assert draw(ladder.server, ladder.runner, engine="scala") is None
    assert add_computer(ladder).status_code == 200
    assert {row.get("computer"): row["games"] for row in standings(ladder)}[_ZERG] == 1


def test_no_bot_may_be_named_as_a_computer(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    refused = upload(server, alice, "computer-Hard-Zerg-Rush", PYTHON_BOT_ZIP)
    assert refused["status"] == 422 and "may not begin with Computer-" in refused["detail"]


def test_the_owner_adds_pauses_and_removes_computers_on_the_settings_page(ladder: LadderWithBots) -> None:
    browser = ladder.server
    sign_in_dev(browser, "alice")
    settings = "/ladders/league/settings"
    fields = {"race": "R", "difficulty": "Harder", "build": "Macro"}
    page = submit(browser, settings, "/ladders/league/computers", fields)
    assert "Added Computer-Harder-Random-Macro" in page.text and "Computer-Harder-Random-Macro</td>" in page.text
    assert '2 <span class="muted">and 1 computer</span>' in browser.get("/").text
    page = submit(browser, settings, "/ladders/league/computers/Computer-Harder-Random-Macro/pause", {})
    assert "Paused Computer-Harder-Random-Macro" in page.text
    ladder_page = browser.get("/ladders/league").text
    assert "Computer-Harder-Random-Macro</td>" in ladder_page and "paused by alice" in ladder_page
    page = submit(browser, settings, "/ladders/league/computers/Computer-Harder-Random-Macro/remove", {})
    assert "Deleted Computer-Harder-Random-Macro" in page.text


def test_the_ladder_s_pages_show_a_match_against_the_computer(ladder: LadderWithBots) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    match = draw(ladder.server, ladder.runner, engine="scala")
    assert match is not None
    report(ladder.server, ladder.runner, match["id"], "Player1Win")
    matches_page = ladder.server.get("/ladders/league/matches").text
    assert f"v1</span> vs {_ZERG}" in matches_page and "basic_bot won" in matches_page
    match_page = ladder.server.get(f"/matches/{match['id']}").text
    assert f"{_ZERG}, the game's built-in computer, Z" in match_page


def test_the_cli_adds_lists_and_removes_computers(
    ladder: LadderWithBots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    token = ladder.alice["Authorization"].removeprefix("Bearer ")
    assert main(["login", "http://localhost", "--token", token], http=ladder.server) == 0
    capsys.readouterr()
    command = ["ladder", "add-computer", "league", "--race", "zerg", "--difficulty", "veryhard", "--build", "rush"]
    assert main(command, http=ladder.server) == 0
    out = capsys.readouterr().out
    assert "2 bots and 1 computer on" in out and f"  {_ZERG}, the game's" in out
    assert main(["ladder", "standings", "league"], http=ladder.server) == 0
    assert f". {_ZERG}: unrated, 0 games, 0-0-0" in capsys.readouterr().out
    assert main(["ladder", "pause-computer", "league", _ZERG], http=ladder.server) == 0
    assert f"  {_ZERG}, the game's, paused" in capsys.readouterr().out
    assert main(["ladder", "remove-computer", "league", _ZERG], http=ladder.server) == 0
    assert capsys.readouterr().out == f"Deleted {_ZERG}\n"


def test_a_runner_plays_the_computer_it_is_offered_and_reports_its_bot_s_logs_alone(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    engine = FakeEngine()
    assert runner_of(ladder, engine, tmp_path).play_next() is True
    [match] = engine.matches
    assert match.player_1.name == "basic_bot"
    assert match.player_2 == Computer(Race.ZERG, Difficulty.VERY_HARD, Build.RUSH)
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert played["result"] == "Player1Win"
    logs = ladder.server.get(f"/api/matches/{played['id']}/logs/1", headers=ladder.alice)
    (tmp_path / "logs.zip").write_bytes(logs.content)
    assert zipfile.ZipFile(tmp_path / "logs.zip").read("stderr.log") == b"basic_bot played"


def test_a_runner_on_ai_arena_s_engine_is_offered_no_computer(ladder: LadderWithBots, tmp_path: Path) -> None:
    withdraw_bobs_bot(ladder)
    add_computer(ladder)
    engine = FakeEngine()
    engine.name = "aiarena"
    assert runner_of(ladder, engine, tmp_path).play_next() is False
    assert engine.matches == []
