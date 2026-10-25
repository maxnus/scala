"""The pages' forms for bots and entries, used as a browser would: signed in, with the page's form token."""

import random
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import PYTHON_BOT_ZIP, csrf_of, sign_in_dev, zip_of
from tests.match_helpers import LadderWithBots, ladder_with_bots
from tests.test_bots_api import upload
from tests.test_ladders_api import create_ladder


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(9)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def submit(
    browser: TestClient, form_page: str, action: str, fields: dict[str, str], *, files: dict[str, Any] | None = None
) -> httpx.Response:
    """Sends the form at `action` as it was filled in on `form_page`, and follows its answer to the page it names."""
    sent = browser.post(
        action,
        data={**fields, "csrf_token": csrf_of(browser, form_page)},
        files=files,
        headers={"Referer": f"http://localhost{form_page}"},
        follow_redirects=False,
    )
    assert sent.status_code == 303, sent.text
    return browser.get(sent.headers["location"])


def zip_file(content: bytes = PYTHON_BOT_ZIP) -> dict[str, Any]:
    return {"archive": ("bot.zip", content, "application/zip")}


def entry_of(ladder: LadderWithBots, bot: str) -> dict[str, Any]:
    entries = ladder.server.get("/api/ladders/league", headers=ladder.alice).json()["entries"]
    return next(entry for entry in entries if entry["bot"] == bot)


def test_a_bot_is_uploaded_from_the_account_page_which_says_so_once(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    page = submit(server, "/account", "/bots", {"name": "new_bot", "race": "Z"}, files=zip_file())
    assert str(page.url).endswith("/bots/new_bot")
    assert '<p class="notice" role="status">Uploaded new_bot, version 1</p>' in page.text
    assert "Uploaded new_bot" not in server.get("/bots/new_bot").text


def test_a_refused_new_bot_comes_back_with_its_reason_and_what_was_typed(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    page = submit(server, "/account", "/bots", {"name": "lost_bot", "race": "P"}, files=zip_file(zip_of({"x": b""})))
    assert str(page.url).endswith("/account")
    assert '<p class="notice refused" role="alert">No entry point for a bot named' in page.text
    assert 'value="lost_bot"' in page.text
    assert '<option value="P" selected>Protoss</option>' in page.text


def test_a_form_without_the_pages_token_is_refused(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    refused = server.post("/bots", data={"name": "a_bot", "race": "T", "csrf_token": "forged"}, files=zip_file())
    assert refused.status_code == 403
    assert "The form has expired" in refused.text


def test_a_form_sent_signed_out_signs_in_and_comes_back_to_its_page(ladder: LadderWithBots) -> None:
    sent = ladder.server.post(
        "/ladders/league/entries",
        data={"bot": "basic_bot", "csrf_token": "none"},
        headers={"Referer": "http://localhost/ladders/league"},
        follow_redirects=False,
    )
    assert (sent.status_code, sent.headers["location"]) == (303, "/sign-in?next=%2Fladders%2Fleague")


def test_its_author_alone_uploads_a_new_version_and_changes_the_race(ladder: LadderWithBots) -> None:
    server = ladder.server
    sign_in_dev(server, "bob")
    assert 'action="/bots/basic_bot/versions"' not in server.get("/bots/basic_bot").text
    page = submit(server, "/bots/bobs_bot", "/bots/basic_bot/versions", {"race": ""}, files=zip_file(b"x"))
    assert "basic_bot is alice&#39;s" in page.text
    sign_in_dev(server, "alice")
    new = zip_of({"run.py": b"print('version 2')"})
    page = submit(server, "/bots/basic_bot", "/bots/basic_bot/versions", {"race": "R"}, files=zip_file(new))
    assert "Uploaded version 2" in page.text
    page = submit(server, "/bots/basic_bot", "/bots/basic_bot/versions", {"race": ""}, files=zip_file(new))
    assert "That zip is version 2 already, so no version was added" in page.text
    bot = server.get("/api/bots/basic_bot", headers=ladder.alice).json()
    assert (bot["race"], len(bot["versions"])) == ("R", 2)


def test_a_protected_ladder_is_joined_by_its_password(server: TestClient) -> None:
    ladder_with_bots(server, ladder="secret", password="pw")
    sign_in_dev(server, "carol")
    page = submit(server, "/ladders/secret", "/ladders/secret/join", {"password": "guess"})
    assert (page.status_code, "That is not secret&#39;s password" in page.text) == (403, True)
    page = submit(server, "/ladders/secret", "/ladders/secret/join", {"password": "pw"})
    assert (page.status_code, "You are a member of secret" in page.text) == (200, True)
    assert "<h2>Standings</h2>" in page.text


def test_a_bot_is_entered_from_the_ladders_page_or_its_own(ladder: LadderWithBots) -> None:
    server = ladder.server
    upload(server, ladder.alice, "second_bot", PYTHON_BOT_ZIP)
    create_ladder(server, ladder.alice, "other")
    sign_in_dev(server, "alice")
    league = server.get("/ladders/league").text
    assert "<option>second_bot</option>" in league and "<option>basic_bot</option>" not in league
    page = submit(
        server, "/ladders/league", "/ladders/league/entries", {"bot": "second_bot", "next_page": "/ladders/league"}
    )
    assert "Entered second_bot in league" in page.text
    assert "Every bot of yours with a version is in this ladder" in page.text
    assert "second_bot" in [
        entry["bot"] for entry in server.get("/api/ladders/league", headers=ladder.alice).json()["entries"]
    ]
    bot_page = server.get("/bots/second_bot").text
    assert "<option>other</option>" in bot_page and "<option>league</option>" not in bot_page
    page = submit(server, "/bots/second_bot", "/bots/second_bot/entries", {"ladder": "other"})
    assert "Entered second_bot in other" in page.text


def test_an_entrys_author_pauses_and_resumes_it_and_the_ladders_owner_withdraws_any(ladder: LadderWithBots) -> None:
    server = ladder.server
    sign_in_dev(server, "bob")
    as_bob = server.get("/ladders/league").text
    assert "/entries/bobs_bot/pause" in as_bob
    assert "/entries/basic_bot/pause" not in as_bob and "/entries/basic_bot/withdraw" not in as_bob
    page = submit(server, "/ladders/league", "/ladders/league/entries/basic_bot/pause", {})
    assert "basic_bot is alice&#39;s" in page.text
    page = submit(server, "/ladders/league", "/ladders/league/entries/bobs_bot/pause", {"next_page": "/bots/bobs_bot"})
    assert str(page.url).endswith("/bots/bobs_bot") and "Paused bobs_bot in league" in page.text
    assert entry_of(ladder, "bobs_bot")["paused_because"] == "paused by bob"
    league = server.get("/ladders/league").text
    assert '<td class="muted">paused by bob</td>' in league
    page = submit(server, "/bots/bobs_bot", "/ladders/league/entries/bobs_bot/resume", {"next_page": "/bots/bobs_bot"})
    assert "bobs_bot plays in league again" in page.text and not entry_of(ladder, "bobs_bot")["paused"]
    sign_in_dev(server, "alice")
    assert "/entries/bobs_bot/withdraw" in server.get("/ladders/league").text
    page = submit(server, "/ladders/league", "/ladders/league/entries/bobs_bot/withdraw", {})
    assert "Took bobs_bot out of league" in page.text
    entries = server.get("/api/ladders/league", headers=ladder.alice).json()["entries"]
    assert [entry["bot"] for entry in entries] == ["basic_bot"]


def test_a_form_comes_back_only_to_a_page_of_this_server(ladder: LadderWithBots) -> None:
    sign_in_dev(ladder.server, "alice")
    sent = ladder.server.post(
        "/ladders/league/entries/basic_bot/pause",
        data={"csrf_token": csrf_of(ladder.server, "/ladders/league"), "next_page": "https://elsewhere.example/"},
        follow_redirects=False,
    )
    assert sent.headers["location"] == "/ladders/league"


def test_a_long_reason_is_cut_short_so_the_session_stays_small(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    unsafe = zip_of({"run.py": b"", "../" + "x" * 5000: b""})
    page = submit(server, "/account", "/bots", {"name": "a_bot", "race": "T"}, files=zip_file(unsafe))
    assert "The zip holds a path that would unpack outside" in page.text
    assert "x" * 400 not in page.text
    assert all(len(cookie.value or "") < 4000 for cookie in server.cookies.jar)


def test_a_notice_shows_on_the_page_it_was_left_for_alone(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    sent = server.post(
        "/bots",
        data={"name": "tab_bot", "race": "T", "csrf_token": csrf_of(server, "/account")},
        files=zip_file(),
        follow_redirects=False,
    )
    assert "Uploaded tab_bot" not in server.get("/account").text
    assert "Uploaded tab_bot, version 1" in server.get(sent.headers["location"]).text


def test_the_new_bot_form_makes_new_bots_alone(ladder: LadderWithBots) -> None:
    sign_in_dev(ladder.server, "alice")
    page = submit(ladder.server, "/account", "/bots", {"name": "Basic_Bot", "race": "Z"}, files=zip_file())
    assert "You have a bot named basic_bot already: upload its next version on its page" in page.text
    bot = ladder.server.get("/api/bots/basic_bot", headers=ladder.alice).json()
    assert (bot["race"], len(bot["versions"])) == ("T", 1)
