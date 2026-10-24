"""A ladder's runners: listing those that played it, and its owner barring one, which voids its results there."""

import random
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from sc2scala.cli import main
from sc2scala.server.database import alembic_config, create_database_engine
from tests.conftest import api_headers_of
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, machine_headers, report
from tests.test_ladders_api import create_ladder


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(13)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def played(ladder: LadderWithBots, runner: dict[str, str], code: str | None = "Player1Win") -> dict[str, Any]:
    """A match drawn for `runner`, reported with `code`, or left being played if `code` is `None`."""
    match = draw(ladder.server, runner)
    assert match is not None
    if code is not None:
        assert report(ladder.server, runner, match["id"], code, with_files=False).status_code == 200
    return match


def bar(
    ladder: LadderWithBots, runner: str, *, keep_results: bool = False, headers: dict[str, str] | None = None
) -> httpx.Response:
    return ladder.server.post(
        "/api/ladders/league/barred-runners",
        headers=headers or ladder.alice,
        json={"runner": runner, "keep_results": keep_results},
    )


def statuses(ladder: LadderWithBots) -> dict[int, tuple[str, str | None]]:
    listed = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    return {match["id"]: (match["status"], match["void_because"]) for match in listed}


def test_a_ladders_runners_are_listed_by_their_finished_matches(ladder: LadderWithBots) -> None:
    tower = machine_headers(ladder.server, ladder.bob, "tower")
    played(ladder, tower)
    for code in ("Player1Win", "Tie", None):
        played(ladder, ladder.runner, code)
    listed = ladder.server.get("/api/ladders/league/runners", headers=ladder.bob).json()
    assert [(runner["runner"], runner["finished"], runner["void"], runner["being_played"]) for runner in listed] == [
        ("alice/desk", 2, 0, 1),
        ("bob/tower", 1, 0, 0),
    ]
    assert all(runner["last_drawn_at"] and runner["barred_at"] is None for runner in listed)


def test_a_barred_runners_results_and_matches_there_are_void_and_the_ratings_fitted_without_them(
    ladder: LadderWithBots,
) -> None:
    tower = machine_headers(ladder.server, ladder.bob, "tower")
    kept = played(ladder, tower)
    voided = [played(ladder, ladder.runner), played(ladder, ladder.runner)]
    being_played = played(ladder, ladder.runner, code=None)
    games_before = [
        row["games"] for row in ladder.server.get("/api/ladders/league/standings", headers=ladder.alice).json()
    ]
    response = bar(ladder, "alice/desk")
    assert response.status_code == 200, response.text
    assert response.json()["results_voided"] == 2
    assert statuses(ladder) == {
        kept["id"]: ("finished", None),
        **{match["id"]: ("void", "its runner was barred by the ladder's owner") for match in voided},
        being_played["id"]: ("void", "its runner was barred by the ladder's owner while it played"),
    }
    standings = ladder.server.get("/api/ladders/league/standings", headers=ladder.alice).json()
    assert (games_before, [row["games"] for row in standings]) == ([3, 3], [1, 1])


def test_a_barred_runner_draws_no_match_of_the_ladder_but_of_others(ladder: LadderWithBots) -> None:
    assert bar(ladder, "alice/desk").status_code == 200
    assert draw(ladder.server, ladder.runner) is None
    create_ladder(ladder.server, ladder.alice, "other")
    for headers, bot in ((ladder.alice, "basic_bot"), (ladder.bob, "bobs_bot")):
        ladder.server.post("/api/ladders/other/entries", headers=headers, json={"bot": bot})
    match = draw(ladder.server, ladder.runner)
    assert match is not None and match["ladder"] == "other"


def test_a_result_of_a_match_leased_before_the_bar_is_refused(ladder: LadderWithBots) -> None:
    match = played(ladder, ladder.runner, code=None)
    bar(ladder, "alice/desk")
    response = report(ladder.server, ladder.runner, match["id"], "Player1Win", with_files=False)
    assert response.status_code == 409
    assert "barred" in response.json()["detail"]


def test_a_runner_barred_for_its_speed_keeps_its_results(ladder: LadderWithBots) -> None:
    finished = played(ladder, ladder.runner)
    being_played = played(ladder, ladder.runner, code=None)
    assert bar(ladder, "alice/desk", keep_results=True).json()["results_voided"] == 0
    assert statuses(ladder)[finished["id"]] == ("finished", None)
    assert statuses(ladder)[being_played["id"]][0] == "void"
    [listed] = ladder.server.get("/api/ladders/league/runners", headers=ladder.alice).json()
    assert listed["barred_at"] is not None


def test_only_the_ladders_owner_bars_and_unbars_a_runner(ladder: LadderWithBots) -> None:
    voided = played(ladder, ladder.runner)
    assert bar(ladder, "alice/desk", headers=ladder.bob).status_code == 403
    assert bar(ladder, "carol/laptop").status_code == 404
    assert bar(ladder, "alice/desk").status_code == 200
    assert bar(ladder, "alice/desk").status_code == 409
    unbar = "/api/ladders/league/barred-runners/alice/desk"
    assert ladder.server.delete(unbar, headers=ladder.bob).status_code == 403
    assert ladder.server.delete(unbar, headers=ladder.alice).status_code == 200
    assert ladder.server.delete(unbar, headers=ladder.alice).status_code == 404
    assert draw(ladder.server, ladder.runner) is not None
    assert statuses(ladder)[voided["id"]][0] == "void"


def test_a_bar_lets_the_entries_its_failed_starts_paused_play_again(ladder: LadderWithBots) -> None:
    for _ in range(5):
        played(ladder, ladder.runner, "InitializationError")
    entries = ladder.server.get("/api/ladders/league", headers=ladder.alice).json()["entries"]
    assert [entry["paused_because"] for entry in entries] == ["failed to start 5 times in a row"] * 2
    ladder.server.post("/api/ladders/league/entries/bobs_bot/pause", headers=ladder.bob)
    bar(ladder, "alice/desk")
    entries = ladder.server.get("/api/ladders/league", headers=ladder.alice).json()["entries"]
    assert [(entry["bot"], entry["paused"], entry["paused_because"]) for entry in entries] == [
        ("basic_bot", False, None),
        ("bobs_bot", True, "paused by bob"),
    ]


def test_a_removed_machine_can_be_barred_and_its_name_stays_barred(ladder: LadderWithBots) -> None:
    voided = played(ladder, ladder.runner)
    ladder.server.delete("/api/runners/desk", headers=ladder.alice)
    assert bar(ladder, "alice/desk").json()["results_voided"] == 1
    assert statuses(ladder)[voided["id"]][0] == "void"
    desk_again = machine_headers(ladder.server, ladder.alice, "desk")
    assert draw(ladder.server, desk_again) is None
    [machine] = ladder.server.get("/api/runners", headers=ladder.alice).json()
    assert machine["barred_from"] == ["league"]


def test_ladder_runners_bar_runner_and_unbar_runner(
    ladder: LadderWithBots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    main(
        ["login", "http://localhost", "--token", ladder.alice["Authorization"].removeprefix("Bearer ")],
        http=ladder.server,
    )
    played(ladder, ladder.runner)
    capsys.readouterr()
    assert main(["ladder", "bar-runner", "league", "alice/desk"], http=ladder.server) == 0
    assert capsys.readouterr().out == "Barred alice/desk from league, 1 result void\n"
    assert main(["ladder", "matches", "league"], http=ladder.server) == 0
    assert ", void, Player1Win reported: its runner was barred by the ladder's owner, by alice/desk" in (
        capsys.readouterr().out
    )
    assert main(["ladder", "runners", "league"], http=ladder.server) == 0
    line = capsys.readouterr().out
    assert (
        line.startswith("alice/desk: 0 finished, 1 void, 0 being played, last drawn 20") and ", barred since 20" in line
    )
    assert main(["runner", "list"], http=ladder.server) == 0
    assert "desk: serves every public ladder, barred from league, last seen" in capsys.readouterr().out
    assert main(["ladder", "unbar-runner", "league", "alice/desk"], http=ladder.server) == 0
    assert capsys.readouterr().out == "alice/desk may play league again; what its bar voided stays void\n"
    with pytest.raises(SystemExit):
        main(["ladder", "bar-runner", "league", "desk"], http=ladder.server)


def test_the_migration_names_the_runner_of_every_match_played_before_it(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'scala.db'}")
    config = alembic_config(engine)
    command.upgrade(config, "b9529115a290")
    with engine.begin() as connection:
        for statement in (
            "INSERT INTO users (id, login, created_at) VALUES (1, 'alice', '2026-10-24 12:00:00')",
            "INSERT INTO runner_machines (id, owner_id, name, token_hash, serves_public, created_at)"
            " VALUES (1, 1, 'desk', 'hash', 1, '2026-10-24 12:00:00')",
            "INSERT INTO bots (id, name, race, owner_id, created_at) VALUES (1, 'basic_bot', 'T', 1, '2026-10-24')",
            "INSERT INTO bot_versions (id, bot_id, number, sha256, size_bytes, bot_type, uploaded_at)"
            " VALUES (1, 1, 1, 'sha', 10, 'python', '2026-10-24')",
            "INSERT INTO maps (id, name, sha256, size_bytes, uploaded_at)"
            " VALUES (1, 'PylonAIE_v4', 'sha', 10, '2026-10-24')",
            "INSERT INTO ladders (id, name, owner_id, game_limits, resource_limits, created_at)"
            " VALUES (1, 'league', 1, '{}', '{}', '2026-10-24')",
            *(
                "INSERT INTO matches (id, ladder_id, map_id, player_1_version_id, player_2_version_id, player_1_race,"
                f" player_2_race, machine_id, status, drawn_at, deadline) VALUES ({match_id}, 1, 1, 1, 1, 'T', 'T',"
                f" {machine_id}, 'finished', '2026-10-24', '2026-10-24')"
                for match_id, machine_id in ((1, "1"), (2, "NULL"))
            ),
        ):
            connection.execute(text(statement))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, runner FROM matches ORDER BY id")).all() == [
            (1, "alice/desk"),
            (2, None),
        ]
    engine.dispose()


def test_a_ladders_runners_are_its_members_to_see(server: TestClient) -> None:
    secret = ladder_with_bots(server, ladder="secret", password="pw")
    carol = api_headers_of(server, "carol")
    assert server.get("/api/ladders/secret/runners", headers=carol).status_code == 403
    assert server.get("/api/ladders/secret/runners", headers=secret.bob).status_code == 200
