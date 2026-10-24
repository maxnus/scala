"""A match drawn by the server, played by a runner on the real engine, and reported back."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc2scala import AiArenaEngine
from sc2scala.runner import Runner
from sc2scala.scala_client import ScalaClient
from tests.bots import assemble_bot
from tests.conftest import api_headers_of
from tests.match_helpers import machine_headers
from tests.test_bots_api import upload
from tests.test_ladders_api import create_ladder

pytestmark = pytest.mark.integration


def zipped_bot(bot: str, tmp_path: Path) -> bytes:
    folder = assemble_bot(bot, tmp_path / "bots")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in folder.iterdir():
            archive.write(path, path.name)
    return buffer.getvalue()


def test_a_runner_plays_the_servers_match_on_the_engine(server: TestClient, map_file: Path, tmp_path: Path) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    create_ladder(server, alice, "league", maps={map_file.name: map_file.read_bytes()})
    upload(server, alice, "leaver_bot", zipped_bot("leaver_bot", tmp_path), race="T")
    upload(server, bob, "marine_bot", zipped_bot("marine_bot", tmp_path), race="T")
    for headers, bot in ((alice, "leaver_bot"), (bob, "marine_bot")):
        server.post("/api/ladders/league/entries", headers=headers, json={"bot": bot})
    token = machine_headers(server, alice)["Authorization"].removeprefix("Bearer ")
    runner = Runner(
        ScalaClient("http://localhost", token, http=server),
        AiArenaEngine(),
        cache_folder=tmp_path / "cache",
        work_folder=tmp_path / "work",
    )
    assert runner.play_next() is True
    [played] = server.get("/api/ladders/league/matches", headers=bob).json()
    marines_seat = next(seat for seat, player in enumerate(played["players"], start=1) if player["bot"] == "marine_bot")
    assert (played["status"], played["result"]) == ("finished", f"Player{marines_seat}Win")
    assert server.get(f"/api/matches/{played['id']}/replay", headers=bob).content.startswith(b"MPQ")
    assert server.get(f"/api/matches/{played['id']}/logs/{marines_seat}", headers=bob).status_code == 200
