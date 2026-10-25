"""The pages and forms of a ladder's owner, and of a runner machine's, used as a browser would."""

import random
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MAP_BYTES, sign_in_dev
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, report
from tests.test_bot_forms import submit


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(17)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def map_files(*names: str, content: bytes = MAP_BYTES) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("maps", (f"{name}.SC2Map", content + name.encode(), "application/octet-stream")) for name in names]


def details_of(ladder: LadderWithBots, name: str = "league") -> dict[str, Any]:
    return ladder.server.get(f"/api/ladders/{name}", headers=ladder.alice).json()


def test_making_a_ladder_needs_signing_in(server: TestClient) -> None:
    refused = server.get("/ladders/new", follow_redirects=False)
    assert (refused.status_code, refused.headers["location"]) == (303, "/sign-in?next=%2Fladders%2Fnew")
    assert "Make a ladder" not in server.get("/").text


def test_a_ladder_is_made_of_its_maps_and_limits_from_the_page(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    assert '<a class="button" href="/ladders/new">Make a ladder</a>' in server.get("/").text
    form = server.get("/ladders/new").text
    assert 'name="max_game_steps" value="80640"' in form and 'name="bot_cpus" value="2.0"' in form
    page = submit(
        server, "/ladders/new", "/ladders/new", {"name": "night", "bot_memory_mib": "2048"}, files=map_files("A", "B")
    )
    assert str(page.url).endswith("/ladders/night") and "Made night" in page.text
    made = server.get("/ladders/night/settings").text
    assert 'name="bot_memory_mib" value="2048"' in made and "A</td>" in made and "B</td>" in made
    assert "Public: anyone sees it" in made


def test_a_refused_ladder_comes_back_with_its_reason_its_name_and_its_limits(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    fields = {"name": "night", "max_step_time_ms": "60"}
    page = submit(server, "/ladders/new", "/ladders/new", fields, files=map_files("A", content=b"not a map"))
    assert str(page.url).endswith("/ladders/new") and "The upload is not a StarCraft II map" in page.text
    assert 'value="night"' in page.text and 'name="max_step_time_ms" value="60"' in page.text
    page = submit(server, "/ladders/new", "/ladders/new", {"name": "New"}, files=map_files("A"))
    assert "&#39;New&#39; names the page that makes ladders" in page.text


def test_a_protected_ladder_is_made_with_its_password(server: TestClient) -> None:
    sign_in_dev(server, "alice")
    submit(server, "/ladders/new", "/ladders/new", {"name": "secret", "password": "pw"}, files=map_files("A"))
    sign_in_dev(server, "bob")
    assert server.get("/ladders/secret").status_code == 403


def test_the_settings_are_the_owners_alone(ladder: LadderWithBots) -> None:
    sign_in_dev(ladder.server, "bob")
    assert "/ladders/league/settings" not in ladder.server.get("/ladders/league").text
    refused = ladder.server.get("/ladders/league/settings")
    assert (refused.status_code, "league is alice&#39;s to change" in refused.text) == (403, True)
    sign_in_dev(ladder.server, "alice")
    assert '<a href="/ladders/league/settings">settings</a>' in ladder.server.get("/ladders/league").text


def test_the_owner_protects_and_opens_the_ladder(ladder: LadderWithBots) -> None:
    server = ladder.server
    sign_in_dev(server, "alice")
    settings = "/ladders/league/settings"
    page = submit(server, settings, "/ladders/league/password", {"new_password": ""})
    assert "A password cannot be empty" in page.text
    page = submit(server, settings, "/ladders/league/password", {"new_password": "pw"})
    assert "league has a new password" in page.text and details_of(ladder)["protected"]
    page = submit(server, settings, "/ladders/league/password/remove", {})
    assert "league is public now" in page.text and not details_of(ladder)["protected"]


def test_the_owner_changes_the_limits_which_must_fit(ladder: LadderWithBots) -> None:
    server = ladder.server
    sign_in_dev(server, "alice")
    settings = "/ladders/league/settings"
    page = submit(server, settings, "/ladders/league/limits", {"bot_cpus": "1.5", "bot_log_mib": "50"})
    assert "Changed the limits" in page.text
    limits = details_of(ladder)["resource_limits"]
    assert (limits["bot_cpus"], limits["bot_log_mib"]) == (1.5, 50)
    page = submit(server, settings, "/ladders/league/limits", {"bot_processes": "0"})
    assert "bot_processes is a positive whole number" in page.text
    assert details_of(ladder)["resource_limits"]["bot_processes"] == 1024


def test_the_owner_adds_maps_and_takes_them_out_but_the_last(ladder: LadderWithBots) -> None:
    server = ladder.server
    sign_in_dev(server, "alice")
    settings = "/ladders/league/settings"
    page = submit(server, settings, "/ladders/league/maps", {}, files=map_files("C", "D"))
    assert "Added C.SC2Map, D.SC2Map" in page.text
    for name in ("C", "D", "PylonAIE_v4"):
        assert f"Took {name} out" in submit(server, settings, f"/ladders/league/maps/{name}/remove", {}).text
    assert [map_file["name"] for map_file in details_of(ladder)["maps"]] == ["TorchesAIE_v4"]
    page = server.get(settings).text
    assert "its last map" in page and "/maps/TorchesAIE_v4/remove" not in page
    page = submit(server, settings, "/ladders/league/maps/TorchesAIE_v4/remove", {})
    assert "TorchesAIE_v4 is league&#39;s last map" in page.text


def test_the_runners_page_lists_them_and_lets_the_owner_bar_one(ladder: LadderWithBots) -> None:
    server = ladder.server
    match = draw(server, ladder.runner)
    assert match is not None
    report(server, ladder.runner, match["id"], "Player1Win", with_files=False)
    sign_in_dev(server, "bob")
    as_bob = server.get("/ladders/league/runners").text
    assert "<td>alice/desk</td>" in as_bob and "Bar it…" not in as_bob
    sign_in_dev(server, "alice")
    runners = "/ladders/league/runners"
    page = submit(server, runners, "/ladders/league/runners/bar", {"runner": "alice/desk", "keep_results": "true"})
    assert "Barred alice/desk, its results kept" in page.text and "its results kept" in page.text
    page = submit(server, runners, "/ladders/league/runners/alice/desk/unbar", {})
    assert "alice/desk may play here again" in page.text
    page = submit(server, runners, "/ladders/league/runners/bar", {"runner": "alice/desk"})
    assert "Barred alice/desk, 1 result void" in page.text
    played = server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert [match["status"] for match in played] == ["void"]


def test_a_machines_owner_chooses_its_ladders_and_removes_it(ladder: LadderWithBots) -> None:
    server = ladder.server
    ladder_with_bots_secret = server.post(
        "/api/ladders",
        headers=ladder.bob,
        data={"name": "bobs-secret", "password": "pw", "limits": "{}"},
        files=map_files("A"),
    )
    assert ladder_with_bots_secret.status_code == 200
    sign_in_dev(server, "alice")
    account = server.get("/account").text
    assert 'name="ladders" value="league"' in account and 'value="bobs-secret"' not in account
    page = submit(server, "/account", "/account/machines/desk", {"ladders": "league"})
    assert "desk serves league" in page.text
    [machine] = server.get("/api/runners", headers=ladder.alice).json()
    assert (machine["serves_public"], machine["ladders"]) == (False, ["league"])
    page = submit(server, "/account", "/account/machines/desk/remove", {})
    assert "Removed desk; the matches it was playing are void" in page.text
    assert server.get("/api/runners", headers=ladder.alice).json() == []


def test_a_machine_cannot_serve_a_ladder_its_owner_may_not_see(ladder: LadderWithBots) -> None:
    server = ladder.server
    server.post(
        "/api/ladders",
        headers=ladder.bob,
        data={"name": "bobs-secret", "password": "pw", "limits": "{}"},
        files=map_files("A"),
    )
    sign_in_dev(server, "alice")
    page = submit(server, "/account", "/account/machines/desk", {"ladders": "bobs-secret", "serves_public": "true"})
    assert "bobs-secret is protected" in page.text
    assert server.get("/api/runners", headers=ladder.alice).json()[0]["ladders"] == []
