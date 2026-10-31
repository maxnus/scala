import json
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import MAP_BYTES, PYTHON_BOT_ZIP, api_headers_of
from tests.test_bots_api import upload


def create_ladder(
    server: TestClient,
    headers: dict[str, str],
    name: str = "league",
    *,
    maps: dict[str, bytes] | None = None,
    password: str | None = None,
    limits: dict[str, Any] | None = None,
) -> Any:
    maps = maps if maps is not None else {"PylonAIE_v4.SC2Map": MAP_BYTES, "TorchesAIE_v4.SC2Map": MAP_BYTES + b"!"}
    data = {"name": name, "limits": json.dumps(limits or {})} | ({"password": password} if password else {})
    files = [("maps", (file_name, content, "application/octet-stream")) for file_name, content in maps.items()]
    return server.post("/api/ladders", headers=headers, data=data, files=files)


def map_rows(server: TestClient) -> int:
    folder = server.app.state.file_store.root / "maps"  # type: ignore[attr-defined]
    return len(list(folder.iterdir()))


def test_a_ladder_is_made_of_its_maps_with_the_default_limits(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    ladder = create_ladder(server, alice, limits={"bot_memory_mib": 2048}).json()
    assert (ladder["name"], ladder["owner"], ladder["protected"], ladder["member"]) == ("league", "alice", False, True)
    assert [map_file["name"] for map_file in ladder["maps"]] == ["PylonAIE_v4", "TorchesAIE_v4"]
    assert ladder["game_limits"]["max_game_steps"] == 80640
    assert ladder["resource_limits"]["bot_memory_mib"] == 2048
    assert ladder["resource_limits"]["bot_cpus"] == 2.0


def test_a_ladders_name_is_unique_in_any_case_and_takes_the_pattern(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    create_ladder(server, alice, "League")
    assert create_ladder(server, alice, "league").status_code == 409
    assert create_ladder(server, alice, "a league").status_code == 422
    assert create_ladder(server, alice, "x" * 41).status_code == 422


def test_a_ladder_needs_maps_that_are_maps(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    assert create_ladder(server, alice, maps={}).status_code == 422
    assert create_ladder(server, alice, maps={"Pylon.SC2Map": b"not a map"}).status_code == 422
    assert create_ladder(server, alice, maps={"Pylon.zip": MAP_BYTES}).status_code == 422
    assert create_ladder(server, alice, maps={"a,b.SC2Map": MAP_BYTES}).status_code == 422


def test_a_limit_must_exist_and_be_a_positive_number(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    assert create_ladder(server, alice, limits={"bot_gpus": 1}).status_code == 422
    assert create_ladder(server, alice, limits={"bot_memory_mib": -1}).status_code == 422
    assert create_ladder(server, alice, limits={"max_game_steps": 1.5}).status_code == 422


def test_a_map_two_ladders_hold_is_kept_once(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    create_ladder(server, alice, "first")
    create_ladder(server, alice, "second")
    assert map_rows(server) == 2


def test_a_protected_ladder_is_shown_to_its_members_alone(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    create_ladder(server, alice, "secret", password="open sesame")
    hidden = {"name": "secret", "owner": "alice", "protected": True, "retired_at": None}
    assert server.get("/api/ladders", headers=bob).json() == [hidden]
    assert server.get("/api/ladders/secret", headers=bob).status_code == 403
    assert server.post("/api/ladders/secret/join", headers=bob, json={"password": "guess"}).status_code == 403
    joined = server.post("/api/ladders/secret/join", headers=bob, json={"password": "open sesame"}).json()
    assert joined["member"] is True
    assert server.get("/api/ladders/secret", headers=bob).status_code == 200


def test_a_bot_is_entered_by_its_author_in_a_ladder_they_see(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    create_ladder(server, alice, "league")
    create_ladder(server, alice, "secret", password="open sesame")
    upload(server, bob, "bobs_bot", PYTHON_BOT_ZIP)
    entered = server.post("/api/ladders/league/entries", headers=bob, json={"bot": "bobs_bot"}).json()
    assert entered["entries"] == [
        {"bot": "bobs_bot", "author": "bob", "version": 1, "paused": False, "paused_because": None}
    ]
    assert server.post("/api/ladders/league/entries", headers=bob, json={"bot": "bobs_bot"}).status_code == 409
    assert server.post("/api/ladders/league/entries", headers=alice, json={"bot": "bobs_bot"}).status_code == 403
    assert server.post("/api/ladders/secret/entries", headers=bob, json={"bot": "bobs_bot"}).status_code == 403


def test_entering_a_public_ladder_keeps_the_author_in_it_once_it_is_protected(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    create_ladder(server, alice, "league")
    upload(server, bob, "bobs_bot", PYTHON_BOT_ZIP)
    server.post("/api/ladders/league/entries", headers=bob, json={"bot": "bobs_bot"})
    server.patch("/api/ladders/league", headers=alice, json={"new_password": "now closed"})
    assert server.get("/api/ladders/league", headers=bob).json()["protected"] is True


def test_a_bot_is_withdrawn_by_its_author_or_the_ladders_owner(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    carol = api_headers_of(server, "carol")
    create_ladder(server, alice, "league")
    upload(server, bob, "bobs_bot", PYTHON_BOT_ZIP)
    server.post("/api/ladders/league/entries", headers=bob, json={"bot": "bobs_bot"})
    assert server.delete("/api/ladders/league/entries/bobs_bot", headers=carol).status_code == 403
    assert server.delete("/api/ladders/league/entries/bobs_bot", headers=alice).json()["entries"] == []
    server.post("/api/ladders/league/entries", headers=bob, json={"bot": "bobs_bot"})
    assert server.delete("/api/ladders/league/entries/BOBS_BOT", headers=bob).json()["entries"] == []


def test_a_paused_entry_resumes_with_its_failed_starts_forgotten(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    create_ladder(server, alice, "league")
    upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)
    server.post("/api/ladders/league/entries", headers=alice, json={"bot": "basic_bot"})
    [paused] = server.post("/api/ladders/league/entries/basic_bot/pause", headers=alice).json()["entries"]
    assert (paused["paused"], paused["paused_because"]) == (True, "paused by alice")
    [resumed] = server.post("/api/ladders/league/entries/basic_bot/resume", headers=alice).json()["entries"]
    assert (resumed["paused"], resumed["paused_because"]) == (False, None)


def test_the_owner_alone_changes_the_ladder(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    create_ladder(server, alice, "league")
    changed = server.patch("/api/ladders/league", headers=alice, json={"limits": {"max_real_time_seconds": 3600}})
    assert changed.json()["game_limits"]["max_real_time_seconds"] == 3600
    assert server.patch("/api/ladders/league", headers=bob, json={"remove_password": True}).status_code == 403
    assert server.patch("/api/ladders/league", headers=alice, json={"limits": {"cores": 1}}).status_code == 422
    protected = server.patch("/api/ladders/league", headers=alice, json={"new_password": "pw"}).json()
    assert protected["protected"] is True
    public = server.patch("/api/ladders/league", headers=alice, json={"remove_password": True}).json()
    assert public["protected"] is False


def test_the_owner_adds_and_removes_maps_but_not_the_last(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    create_ladder(server, alice, "league", maps={"PylonAIE_v4.SC2Map": MAP_BYTES})
    added = server.post(
        "/api/ladders/league/maps",
        headers=alice,
        files={"map_file": ("UltraloveAIE_v2.SC2Map", MAP_BYTES + b"2", "application/octet-stream")},
    )
    assert [map_file["name"] for map_file in added.json()["maps"]] == ["PylonAIE_v4", "UltraloveAIE_v2"]
    other_pylon = server.post(
        "/api/ladders/league/maps",
        headers=alice,
        files={"map_file": ("PylonAIE_v4.SC2Map", MAP_BYTES + b"other", "application/octet-stream")},
    )
    assert other_pylon.status_code == 409
    assert server.delete("/api/ladders/league/maps/UltraloveAIE_v2", headers=alice).status_code == 200
    assert server.delete("/api/ladders/league/maps/PylonAIE_v4", headers=alice).status_code == 409


def test_each_ladder_holds_a_shared_map_under_the_name_it_gave(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    first = create_ladder(server, alice, "first", maps={"Pylon.SC2Map": MAP_BYTES}).json()
    second = create_ladder(server, alice, "second", maps={"PylonAIE_v4.SC2Map": MAP_BYTES}).json()
    assert [map_file["name"] for map_file in first["maps"]] == ["Pylon"]
    assert [map_file["name"] for map_file in second["maps"]] == ["PylonAIE_v4"]
    assert first["maps"][0]["sha256"] == second["maps"][0]["sha256"]
    assert map_rows(server) == 1
