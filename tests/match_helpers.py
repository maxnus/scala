"""A server holding a ladder with bots entered, and a runner machine to draw its matches."""

import json
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import PYTHON_BOT_ZIP, api_headers_of, zip_of
from tests.test_bots_api import upload
from tests.test_ladders_api import create_ladder

REPLAY_BYTES = b"MPQ\x1b" + b"a replay" * 10


@dataclass
class LadderWithBots:
    """`alice` owns the ladder and `basic_bot`, `bob` owns `bobs_bot`, and `runner` is alice's machine."""

    server: TestClient
    alice: dict[str, str]
    bob: dict[str, str]
    runner: dict[str, str]


def machine_headers(
    server: TestClient, owner: dict[str, str], name: str = "desk", **registration: Any
) -> dict[str, str]:
    """The headers with which a machine of `owner`'s, registered now, calls the runner API."""
    response = server.post("/api/runners", headers=owner, json={"name": name, **registration})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def ladder_with_bots(server: TestClient, *, ladder: str = "league", password: str | None = None) -> LadderWithBots:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    create_ladder(server, alice, ladder, password=password, limits={"max_real_time_seconds": 600})
    if password:
        server.post(f"/api/ladders/{ladder}/join", headers=bob, json={"password": password})
    upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)
    upload(server, bob, "bobs_bot", zip_of({"run.py": b"print('bob')"}), race="Z")
    for headers, bot in ((alice, "basic_bot"), (bob, "bobs_bot")):
        assert server.post(f"/api/ladders/{ladder}/entries", headers=headers, json={"bot": bot}).status_code == 200
    return LadderWithBots(server=server, alice=alice, bob=bob, runner=machine_headers(server, alice))


def draw(server: TestClient, runner: dict[str, str]) -> dict[str, Any] | None:
    response = server.post("/api/runner/matches", headers=runner)
    assert response.status_code in (200, 204), response.text
    return response.json() if response.status_code == 200 else None


def report(server: TestClient, runner: dict[str, str], match_id: int, code: str, *, with_files: bool = True) -> Any:
    result = {"code": code, "game_steps": 4480, "average_step_times": [0.002, None]}
    files = (
        {
            "replay": ("replay.SC2Replay", REPLAY_BYTES, "application/octet-stream"),
            "player_1_logs": ("player_1.zip", zip_of({"stderr.log": b"player 1 played"}), "application/zip"),
            "player_2_logs": ("player_2.zip", zip_of({"stderr.log": b"player 2 played"}), "application/zip"),
            "engine_logs": ("engine.zip", zip_of({"compose.log": b"compose"}), "application/zip"),
        }
        if with_files
        else None
    )
    return server.post(
        f"/api/runner/matches/{match_id}/result", headers=runner, data={"result": json.dumps(result)}, files=files
    )
