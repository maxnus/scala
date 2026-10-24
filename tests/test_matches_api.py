import random
from collections import Counter
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from sc2scala.server.models import MatchRecord
from tests.conftest import PYTHON_BOT_ZIP, api_headers_of
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, machine_headers, report
from tests.test_bots_api import upload
from tests.test_ladders_api import create_ladder


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(7)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def test_a_runner_token_is_shown_once_and_names_its_machine(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    registered = server.post("/api/runners", headers=alice, json={"name": "desk", "serves_public": False}).json()
    assert registered["token"].startswith("scala_runner_")
    assert server.get("/api/runners", headers=alice).json() == [
        {
            "name": "desk",
            "owner": "alice",
            "serves_public": False,
            "ladders": [],
            "barred_from": [],
            "last_seen_at": None,
        }
    ]
    assert server.post("/api/runners", headers=alice, json={"name": "desk"}).status_code == 409
    assert server.post("/api/runners", headers=alice, json={"name": "my desk"}).status_code == 422


def test_the_runner_api_takes_runner_tokens_alone(ladder: LadderWithBots) -> None:
    assert ladder.server.post("/api/runner/matches", headers=ladder.alice).status_code == 401
    assert ladder.server.get("/api/me", headers=ladder.runner).status_code == 401


def test_a_match_is_drawn_from_the_ladders_two_bots_and_its_maps(ladder: LadderWithBots) -> None:
    before = datetime.now().astimezone()
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    assert match["ladder"] == "league"
    assert {player["bot"] for player in match["players"]} == {"basic_bot", "bobs_bot"}
    assert [player["seat"] for player in match["players"]] == [1, 2]
    races = {player["bot"]: player["race"] for player in match["players"]}
    assert races == {"basic_bot": "T", "bobs_bot": "Z"}
    assert match["map"]["name"] in ("PylonAIE_v4", "TorchesAIE_v4")
    assert match["game_limits"]["max_real_time_seconds"] == 600
    deadline = datetime.fromisoformat(match["deadline"])
    assert timedelta(seconds=1499) < deadline - before < timedelta(seconds=1510)


def test_both_seats_and_every_map_come_up(ladder: LadderWithBots) -> None:
    drawn = [draw(ladder.server, ladder.runner) for _ in range(40)]
    first_seats = Counter(match["players"][0]["bot"] for match in drawn if match)
    maps = Counter(match["map"]["name"] for match in drawn if match)
    assert set(first_seats) == {"basic_bot", "bobs_bot"}
    assert set(maps) == {"PylonAIE_v4", "TorchesAIE_v4"}


def test_a_ladder_is_drawn_by_its_number_of_active_bots(ladder: LadderWithBots) -> None:
    server = ladder.server
    create_ladder(server, ladder.alice, "bigger")
    for owner in ("carol", "dave", "erin", "frank"):
        headers = api_headers_of(server, owner)
        upload(server, headers, f"{owner}_bot", PYTHON_BOT_ZIP)
        server.post("/api/ladders/bigger/entries", headers=headers, json={"bot": f"{owner}_bot"})
    ladders = Counter(match["ladder"] for match in (draw(server, ladder.runner) for _ in range(600)) if match)
    assert 1.6 < ladders["bigger"] / ladders["league"] < 2.5


def test_no_match_is_drawn_without_two_active_bots(ladder: LadderWithBots) -> None:
    ladder.server.post("/api/ladders/league/entries/bobs_bot/pause", headers=ladder.bob)
    assert draw(ladder.server, ladder.runner) is None


def test_a_protected_ladder_is_served_when_listed_and_its_runners_owner_is_a_member(server: TestClient) -> None:
    secret = ladder_with_bots(server, ladder="secret", password="pw")
    carol = api_headers_of(server, "carol")
    assert draw(server, secret.runner) is None
    listing = machine_headers(server, secret.alice, "listing", ladders=["secret"])
    assert draw(server, listing) is not None
    assert server.post("/api/runners", headers=carol, json={"name": "nosy", "ladders": ["secret"]}).status_code == 403


def test_a_runner_downloads_the_files_of_its_matches_alone(ladder: LadderWithBots) -> None:
    other_runner = machine_headers(ladder.server, ladder.bob, "other")
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    bot_sha = match["players"][0]["sha256"]
    map_sha = match["map"]["sha256"]
    assert ladder.server.get(f"/api/runner/bots/{bot_sha}", headers=ladder.runner).status_code == 200
    assert ladder.server.get(f"/api/runner/maps/{map_sha}", headers=ladder.runner).status_code == 200
    assert ladder.server.get(f"/api/runner/bots/{bot_sha}", headers=other_runner).status_code == 403
    assert ladder.server.get(f"/api/runner/maps/{map_sha}", headers=other_runner).status_code == 403


def test_a_result_finishes_the_match_once(ladder: LadderWithBots) -> None:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    assert report(ladder.server, ladder.runner, match["id"], "Player1Win").json()["status"] == "finished"
    assert report(ladder.server, ladder.runner, match["id"], "Player2Win").status_code == 409
    listed = ladder.server.get("/api/ladders/league/matches", headers=ladder.bob).json()
    assert [(played["id"], played["status"], played["result"], played["replay"]) for played in listed] == [
        (match["id"], "finished", "Player1Win", True)
    ]


def test_a_result_after_the_deadline_is_refused_and_the_match_void(ladder: LadderWithBots) -> None:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    with Session(ladder.server.app.state.engine) as session:  # type: ignore[attr-defined]
        session.execute(update(MatchRecord).values(deadline=datetime(2026, 1, 1).astimezone()))
        session.commit()
    response = report(ladder.server, ladder.runner, match["id"], "Player1Win")
    assert response.status_code == 409
    assert "no result by the deadline" in response.json()["detail"]


def test_a_runner_that_cannot_play_voids_the_match(ladder: LadderWithBots) -> None:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    response = ladder.server.post(
        f"/api/runner/matches/{match['id']}/failure", headers=ladder.runner, json={"reason": "Docker is missing"}
    )
    assert response.json()["status"] == "void"
    played = ladder.server.get(f"/api/matches/{match['id']}", headers=ladder.alice).json()
    assert played["void_because"] == "its runner could not play it: Docker is missing"


def test_removing_a_machine_voids_its_matches_which_still_name_it(ladder: LadderWithBots) -> None:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    ladder.server.delete("/api/runners/desk", headers=ladder.alice)
    played = ladder.server.get(f"/api/matches/{match['id']}", headers=ladder.alice).json()
    assert (played["status"], played["runner"]) == ("void", "alice/desk")
    assert ladder.server.post("/api/runner/matches", headers=ladder.runner).status_code == 401


def test_five_failed_starts_in_a_row_pause_both_entries_and_a_start_forgets_them(ladder: LadderWithBots) -> None:
    for _ in range(4):
        match = draw(ladder.server, ladder.runner)
        assert match is not None
        report(ladder.server, ladder.runner, match["id"], "InitializationError", with_files=False)
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    report(ladder.server, ladder.runner, match["id"], "Player2Crash", with_files=False)
    for _ in range(5):
        match = draw(ladder.server, ladder.runner)
        assert match is not None
        report(ladder.server, ladder.runner, match["id"], "InitializationError", with_files=False)
    entries = ladder.server.get("/api/ladders/league", headers=ladder.alice).json()["entries"]
    assert [(entry["paused"], entry["paused_because"]) for entry in entries] == [
        (True, "failed to start 5 times in a row")
    ] * 2
    assert draw(ladder.server, ladder.runner) is None


def test_a_replay_is_for_the_ladder_and_a_bots_logs_for_its_author(ladder: LadderWithBots) -> None:
    server = ladder.server
    match = draw(server, ladder.runner)
    assert match is not None
    report(server, ladder.runner, match["id"], "Tie")
    alices_seat = next(player["seat"] for player in match["players"] if player["bot"] == "basic_bot")
    bobs_seat = 3 - alices_seat
    assert server.get(f"/api/matches/{match['id']}/replay", headers=ladder.bob).content.startswith(b"MPQ")
    assert server.get(f"/api/matches/{match['id']}/logs/{alices_seat}", headers=ladder.alice).status_code == 200
    assert server.get(f"/api/matches/{match['id']}/logs/{bobs_seat}", headers=ladder.alice).status_code == 403
    assert server.get(f"/api/matches/{match['id']}/logs/engine", headers=ladder.alice).status_code == 200
    assert server.get(f"/api/matches/{match['id']}/logs/engine", headers=ladder.bob).status_code == 403


def test_a_result_must_name_a_result_code(ladder: LadderWithBots) -> None:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    assert report(ladder.server, ladder.runner, match["id"], "Victory").status_code == 422
