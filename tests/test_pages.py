import random
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sc2scala.server.models import utc_now
from sc2scala.server.storage_tidying import drop_old_logs
from tests.conftest import sign_in_dev
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, report


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(5)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def play_one(ladder: LadderWithBots, code: str = "Player1Win") -> dict:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    report(ladder.server, ladder.runner, match["id"], code)
    return match


def test_the_home_page_lists_the_ladders(ladder: LadderWithBots) -> None:
    ladder_with_bots_page = ladder.server.get("/").text
    assert '<a href="/ladders/league">league</a>' in ladder_with_bots_page


def test_anyone_sees_a_public_ladders_standings_and_matches(ladder: LadderWithBots) -> None:
    match = play_one(ladder)
    page = ladder.server.get("/ladders/league").text
    assert "<h2>Standings</h2>" in page
    assert '<a href="/bots/basic_bot">basic_bot</a>' in page
    assert f'<a href="/matches/{match["id"]}">{match["id"]}</a>' in page
    assert f"{match['players'][0]['bot']} won" in page
    assert "± " in page


def test_a_protected_ladder_is_shown_to_its_members_alone_and_offered_to_join(server: TestClient) -> None:
    ladder_with_bots(server, ladder="secret", password="pw")
    home = server.get("/").text
    assert '<a href="/ladders/secret">join it</a> with its password' in home
    refused = server.get("/ladders/secret")
    assert refused.status_code == 403
    assert "Only its members see this ladder" in refused.text and "Sign in to join" in refused.text
    assert "<h2>Standings</h2>" not in refused.text
    sign_in_dev(server, "carol")
    assert 'action="/ladders/secret/join"' in server.get("/ladders/secret").text
    sign_in_dev(server, "bob")
    assert server.get("/ladders/secret").status_code == 200


def test_a_match_page_offers_the_replay_to_all_and_logs_to_their_author(ladder: LadderWithBots) -> None:
    match = play_one(ladder)
    alices_seat = next(player["seat"] for player in match["players"] if player["bot"] == "basic_bot")
    anonymous = ladder.server.get(f"/matches/{match['id']}").text
    assert f'<a href="/matches/{match["id"]}/replay">download</a>' in anonymous
    assert "its logs" not in anonymous
    assert ladder.server.get(f"/matches/{match['id']}/replay").content.startswith(b"MPQ")
    assert ladder.server.get(f"/matches/{match['id']}/logs/{alices_seat}").status_code == 403
    sign_in_dev(ladder.server, "alice")
    as_alice = ladder.server.get(f"/matches/{match['id']}").text
    assert f'<a href="/matches/{match["id"]}/logs/{alices_seat}">its logs</a>' in as_alice
    assert f'/matches/{match["id"]}/logs/{3 - alices_seat}"' not in as_alice
    assert ladder.server.get(f"/matches/{match['id']}/logs/{alices_seat}").status_code == 200


def test_the_pairs_page_shows_each_pairs_record(ladder: LadderWithBots) -> None:
    match = play_one(ladder, "Player1Win")
    winner, loser = (player["bot"] for player in match["players"])
    page = ladder.server.get("/ladders/league/pairs").text

    def row_of(bot: str) -> str:
        start = page.index(f'<th><a href="/bots/{bot}">')
        return page[start : page.index("</tr>", start)]

    assert "1–0–0" in row_of(winner)
    assert "0–1–0" in row_of(loser)


def test_a_bot_page_shows_its_versions_ladders_and_matches(ladder: LadderWithBots) -> None:
    match = play_one(ladder)
    page = ladder.server.get("/bots/basic_bot").text
    assert "<h1>basic_bot</h1>" in page
    assert '<a href="/ladders/league">league</a>' in page
    assert f'<a href="/matches/{match["id"]}">{match["id"]}</a>' in page


def test_the_account_page_lists_ones_bots_and_machines(ladder: LadderWithBots) -> None:
    sign_in_dev(ladder.server, "alice")
    page = ladder.server.get("/account").text
    assert '<a href="/bots/basic_bot">basic_bot</a>' in page
    assert "<td>desk</td>" in page


def test_a_page_not_found_is_a_page_and_the_apis_stays_json(ladder: LadderWithBots) -> None:
    page = ladder.server.get("/ladders/nowhere")
    assert page.status_code == 404
    assert "<h1>Not found</h1>" in page.text
    api = ladder.server.get("/api/ladders/nowhere", headers=ladder.alice)
    assert api.status_code == 404
    assert api.json() == {"detail": "No ladder is named nowhere"}


def test_the_bots_page_lists_every_bot_and_the_header_leads_to_it(ladder: LadderWithBots) -> None:
    page = ladder.server.get("/bots").text
    assert '<a href="/bots/basic_bot">basic_bot</a>' in page and '<a href="/bots/bobs_bot">bobs_bot</a>' in page
    assert '<a href="/bots" aria-current="page">Bots</a>' in page and '<a href="/">Ladders</a>' in page
    assert '<a href="/" aria-current="page">Ladders</a>' in ladder.server.get("/ladders/league").text


def test_a_matchs_logs_are_linked_while_they_are_kept(ladder: LadderWithBots) -> None:
    match = play_one(ladder)
    sign_in_dev(ladder.server, "alice")
    page = ladder.server.get(f"/matches/{match['id']}").text
    assert f'href="/matches/{match["id"]}/logs/engine"' in page
    assert "A match's logs are kept 30 days once it ends" in page
    with Session(ladder.server.app.state.engine) as session:  # type: ignore[attr-defined]
        assert drop_old_logs(session, keep_logs_days=30, now=utc_now() + timedelta(days=31)) == 1
        session.commit()
    page = ladder.server.get(f"/matches/{match['id']}").text
    assert "/logs/" not in page and "A match's logs are kept 30 days" in page
