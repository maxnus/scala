"""A runner machine's controls: how many matches it plays at once and pausing it, set by its owner on the API and their
page, which the server applies when its runner asks; what it plays now; and the leases its runner lost."""

import random
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sc2scala.server.models import RunnerMachine, utc_now
from tests.conftest import csrf_of, sign_in_dev
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(7)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def ask(
    ladder: LadderWithBots, *, playing: list[int], max_matches_at_once: int = 7, next_window_opens: str | None = None
) -> dict[str, Any]:
    """Alice's machine `desk` asking for a match as a runner of step 9 does, naming the matches it plays."""
    asked = {
        "engine": "scala",
        "playing": playing,
        "max_matches_at_once": max_matches_at_once,
        "scala_version": "0.1.0",
        "next_window_opens": next_window_opens,
    }
    response = ladder.server.post("/api/runner/ask", headers=ladder.runner, json=asked)
    assert response.status_code == 200, response.text
    return response.json()


def set_desk(ladder: LadderWithBots, **changes: object) -> Any:
    return ladder.server.patch("/api/runners/desk", headers=ladder.alice, json=changes)


def desk(ladder: LadderWithBots) -> dict[str, Any]:
    [machine] = ladder.server.get("/api/runners", headers=ladder.alice).json()
    return machine


def statuses(ladder: LadderWithBots) -> dict[int, str]:
    return {
        match["id"]: match["status"]
        for match in ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    }


def change_desk_in_the_database(ladder: LadderWithBots, **values: object) -> None:
    with Session(ladder.server.app.state.engine) as session:  # type: ignore[attr-defined]
        session.execute(update(RunnerMachine).where(RunnerMachine.name == "desk").values(**values))
        session.commit()


def test_an_ask_is_answered_with_a_match_and_the_owners_settings_and_what_the_runner_says_is_kept(
    ladder: LadderWithBots,
) -> None:
    answer = ask(ladder, playing=[])
    assert (answer["matches_at_once"], answer["paused"]) == (1, False)
    assert {player["bot"] for player in answer["match"]["players"]} == {"basic_bot", "bobs_bot"}
    machine = desk(ladder)
    assert (machine["max_matches_at_once"], machine["scala_version"], machine["engine"]) == (7, "0.1.0", "scala")
    assert (machine["playing"], machine["idle_because"], machine["follows_its_controls"]) == (
        [answer["match"]["id"]],
        None,
        True,
    )


def test_a_machine_plays_no_more_matches_at_once_than_its_owner_sets(ladder: LadderWithBots) -> None:
    first = ask(ladder, playing=[])["match"]["id"]
    assert ask(ladder, playing=[first])["match"] is None
    assert set_desk(ladder, matches_at_once=2).json()["matches_at_once"] == 2
    second = ask(ladder, playing=[first])
    assert second["matches_at_once"] == 2
    assert ask(ladder, playing=[first, second["match"]["id"]])["match"] is None
    assert set(statuses(ladder).values()) == {"leased"}


def test_nor_more_than_its_runner_allows(ladder: LadderWithBots) -> None:
    assert set_desk(ladder, matches_at_once=3).status_code == 200, "its runner said no most yet"
    first = ask(ladder, playing=[], max_matches_at_once=1)["match"]["id"]
    assert ask(ladder, playing=[first], max_matches_at_once=1)["match"] is None
    refused = set_desk(ladder, matches_at_once=2)
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "desk allows 1 to 1 matches at once, not 2: max_matches_at_once in its runner.toml sets the most"
    )


@pytest.mark.parametrize("matches_at_once", [0, 101])
def test_a_number_of_matches_at_once_out_of_range_is_refused(ladder: LadderWithBots, matches_at_once: int) -> None:
    refused = set_desk(ladder, matches_at_once=matches_at_once)
    assert refused.status_code == 422
    assert refused.json()["detail"] == f"A machine plays 1 to 100 matches at once, not {matches_at_once}"


def test_a_paused_machine_takes_no_match_and_plays_on_once_resumed(ladder: LadderWithBots) -> None:
    ask(ladder, playing=[])
    set_desk(ladder, matches_at_once=2, paused=True)
    first = next(iter(statuses(ladder)))
    answer = ask(ladder, playing=[first])
    assert (answer["match"], answer["paused"]) == (None, True)
    assert desk(ladder)["playing"] == [first], "a pause lets the matches being played end"
    assert ask(ladder, playing=[])["match"] is None
    assert desk(ladder)["idle_because"] == "paused"
    set_desk(ladder, paused=False)
    assert ask(ladder, playing=[])["match"] is not None


def test_outside_its_windows_a_machine_takes_no_match_and_says_when_the_next_opens(ladder: LadderWithBots) -> None:
    assert ask(ladder, playing=[], next_window_opens="2026-11-03T23:00:00")["match"] is None
    assert desk(ladder)["idle_because"] == "outside its windows until Tue 23:00, its time"
    assert ask(ladder, playing=[])["match"] is not None


def test_a_lease_its_runner_does_not_name_at_its_next_ask_is_void(ladder: LadderWithBots) -> None:
    lost = ask(ladder, playing=[])["match"]["id"]
    drawn = ask(ladder, playing=[])["match"]
    assert drawn is not None, "the lost lease no longer counts against its 1 match at once"
    assert statuses(ladder) == {lost: "void", drawn["id"]: "leased"}
    [void] = [
        match
        for match in ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
        if match["id"] == lost
    ]
    assert void["void_because"] == "its runner no longer played it"


def test_a_lease_whose_answer_may_still_be_on_its_way_stays(ladder: LadderWithBots) -> None:
    leased = ask(ladder, playing=[])["match"]["id"]
    change_desk_in_the_database(ladder, last_answered_at=utc_now() + timedelta(seconds=5))
    assert ask(ladder, playing=[])["match"] is None
    assert statuses(ladder) == {leased: "leased"}


def test_a_runner_before_step_9_is_left_as_it_was(ladder: LadderWithBots) -> None:
    ask(ladder, playing=[], next_window_opens="2026-11-03T23:00:00")
    assert desk(ladder)["follows_its_controls"] is True
    set_desk(ladder, paused=True)
    first, second = draw(ladder.server, ladder.runner), draw(ladder.server, ladder.runner)
    assert first is not None and second is not None, "it plays as many at once as its own file says, unpaused"
    machine = desk(ladder)
    assert (machine["follows_its_controls"], machine["playing"]) == (False, [first["id"], second["id"]])


def test_an_ask_naming_more_matches_than_a_machine_may_play_is_refused(ladder: LadderWithBots) -> None:
    asked = {"engine": "scala", "playing": list(range(101)), "max_matches_at_once": 7, "scala_version": "0.1.0"}
    assert ladder.server.post("/api/runner/ask", headers=ladder.runner, json=asked).status_code == 422


def test_a_machine_never_seen_or_not_seen_lately_says_so(ladder: LadderWithBots) -> None:
    assert desk(ladder)["idle_because"] == "never seen"
    ask(ladder, playing=[])
    change_desk_in_the_database(ladder, last_seen_at=utc_now() - timedelta(minutes=6))
    machine = desk(ladder)
    assert (machine["playing"], machine["idle_because"]) == ([], "not seen in the last 5 minutes")


def test_a_machine_seen_lately_playing_nothing_has_no_match_to_play(server: TestClient) -> None:
    lone = ladder_with_bots(server)
    lone.server.post("/api/ladders/league/entries/bobs_bot/pause", headers=lone.bob)
    assert ask(lone, playing=[])["match"] is None
    assert desk(lone)["idle_because"] == "no match to play"


def test_the_owner_sets_the_number_pauses_and_resumes_on_their_page(ladder: LadderWithBots) -> None:
    played = ask(ladder, playing=[], max_matches_at_once=4)["match"]["id"]
    browser = ladder.server
    sign_in_dev(browser, "alice")
    page = browser.get("/account").text
    assert f'<a href="/matches/{played}">match {played}</a>' in page
    assert "of 4 it allows" in page and "Scala 0.1.0, Scala&#39;s engine" in page
    token = csrf_of(browser, "/account")
    answered = browser.post(
        "/account/machines/desk/matches-at-once", data={"csrf_token": token, "matches_at_once": "3"}
    ).text
    assert "desk plays up to 3 matches at once from its runner&#39;s next ask, within 30 s" in answered
    refused = browser.post(
        "/account/machines/desk/matches-at-once", data={"csrf_token": token, "matches_at_once": "5"}
    ).text
    assert "desk allows 1 to 4 matches at once, not 5" in refused
    assert (
        "Matches at once is a whole number, not &#39;many&#39;"
        in browser.post(
            "/account/machines/desk/matches-at-once", data={"csrf_token": token, "matches_at_once": "many"}
        ).text
    )
    assert (
        "Paused desk: it takes no new match"
        in browser.post("/account/machines/desk/pause", data={"csrf_token": token}).text
    )
    assert (desk(ladder)["matches_at_once"], desk(ladder)["paused"]) == (3, True)
    assert "; paused" in browser.get("/account").text
    assert "Resumed desk" in browser.post("/account/machines/desk/resume", data={"csrf_token": token}).text
    assert desk(ladder)["paused"] is False


def test_the_page_says_a_runner_before_step_9_plays_as_its_file_says(ladder: LadderWithBots) -> None:
    draw(ladder.server, ladder.runner)
    sign_in_dev(ladder.server, "alice")
    assert "Its runner predates these settings" in ladder.server.get("/account").text


def test_only_the_owner_changes_their_machine(ladder: LadderWithBots) -> None:
    refused = ladder.server.patch("/api/runners/desk", headers=ladder.bob, json={"paused": True})
    assert refused.status_code == 404
    with Session(ladder.server.app.state.engine) as session:  # type: ignore[attr-defined]
        assert session.scalar(select(RunnerMachine.paused).where(RunnerMachine.name == "desk")) is False
