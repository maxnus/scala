"""Tidying the server's storage: old logs dropped, and the files no row holds removed once their grace has passed."""

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from sc2scala.server.app import create_app
from sc2scala.server.file_store import FileStore
from sc2scala.server.models import MatchRecord, utc_now
from sc2scala.server.storage_tidying import UNHELD_FILE_GRACE, tidy_storage
from tests.conftest import server_config_in, sign_in_dev
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, report

DAY = timedelta(days=1)


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(3)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def play_one(ladder: LadderWithBots) -> int:
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    assert report(ladder.server, ladder.runner, match["id"], "Player1Win").status_code == 200
    return match["id"]


def ended_days_ago(server: TestClient, match_id: int, days: int) -> None:
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        session.execute(
            update(MatchRecord).where(MatchRecord.id == match_id).values(finished_at=utc_now() - days * DAY)
        )
        session.commit()


def files_of(server: TestClient, kind: str) -> list[Path]:
    store: FileStore = server.app.state.file_store  # type: ignore[attr-defined]
    folder = store.root / kind
    return sorted(folder.iterdir()) if folder.is_dir() else []


def make_old(*paths: Path) -> None:
    """Makes the files look kept two days ago, past their grace."""
    old = (utc_now() - 2 * DAY).timestamp()
    for path in paths:
        os.utime(path, (old, old))


def tidy(server: TestClient, *, keep_logs_days: int = 30, now: datetime | None = None) -> tuple[int, int]:
    tidied = tidy_storage(
        server.app.state.engine,  # type: ignore[attr-defined]
        server.app.state.file_store,  # type: ignore[attr-defined]
        keep_logs_days=keep_logs_days,
        now=now or utc_now(),
    )
    return tidied.matches_logs_dropped, tidied.files_removed


def logs_of_match(server: TestClient, match_id: int) -> tuple[str | None, str | None, str | None]:
    with Session(server.app.state.engine) as session:  # type: ignore[attr-defined]
        match = session.get(MatchRecord, match_id)
        assert match is not None
        return match.player_1_logs_sha256, match.player_2_logs_sha256, match.engine_logs_sha256


def test_a_matchs_logs_go_once_old_and_its_replay_stays(ladder: LadderWithBots) -> None:
    server = ladder.server
    old_match, recent_match = play_one(ladder), play_one(ladder)
    ended_days_ago(server, old_match, 31)
    ended_days_ago(server, recent_match, 29)
    make_old(*files_of(server, "logs"), *files_of(server, "replays"))
    old_logs, recent_logs = set(logs_of_match(server, old_match)), set(logs_of_match(server, recent_match))
    assert tidy(server) == (1, len(old_logs - recent_logs)), "a file the recent match holds too stays"
    assert logs_of_match(server, old_match) == (None, None, None)
    assert {path.stem for path in files_of(server, "logs")} == recent_logs
    ended_days_ago(server, recent_match, 31)
    assert tidy(server) == (1, len(recent_logs))
    assert files_of(server, "logs") == []
    assert len(files_of(server, "replays")) == 1, "both matches' replay, kept once by its hash"


def test_no_log_goes_when_logs_are_kept_for_good(ladder: LadderWithBots) -> None:
    server = ladder.server
    match_id = play_one(ladder)
    ended_days_ago(server, match_id, 400)
    make_old(*files_of(server, "logs"))
    assert tidy(server, keep_logs_days=0) == (0, 0)
    assert None not in logs_of_match(server, match_id)


def test_an_unheld_file_stays_through_its_grace_and_a_strangers_file_for_good(server: TestClient) -> None:
    store: FileStore = server.app.state.file_store  # type: ignore[attr-defined]
    (store.root / "bots").mkdir(parents=True)
    unheld = store.root / "bots" / f"{'a' * 64}.zip"
    partial = store.root / "bots" / ".upload-abc"
    stranger = store.root / "bots" / "notes.txt"
    for path in (unheld, partial, stranger):
        path.write_bytes(b"x")
    assert tidy(server) == (0, 0), "an upload whose row is not written yet looks unheld"
    assert tidy(server, now=utc_now() + UNHELD_FILE_GRACE + DAY) == (0, 2)
    assert files_of(server, "bots") == [stranger]


def test_the_server_tidies_when_it_starts(tmp_path: Path) -> None:
    config = server_config_in(tmp_path / "server")
    unheld = config.data_folder / "files" / "replays" / f"{'b' * 64}.SC2Replay"
    unheld.parent.mkdir(parents=True)
    unheld.write_bytes(b"MPQ")
    make_old(unheld)
    with TestClient(create_app(config), base_url="http://localhost") as server:
        sign_in_dev(server, "alice")
    assert not unheld.exists()
