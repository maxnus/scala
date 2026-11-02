"""A runner playing several matches at once, as many as the server says, in its windows, stopping, and keeping its
folders small."""

import dataclasses
import os
import random
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sc2scala import EngineError, Match, MatchResult, MatchStoppedError, ScalaError
from sc2scala.runner import Runner
from sc2scala.runner_config import PlayingSchedule, PlayingWindow
from tests.match_helpers import LadderWithBots, ladder_with_bots
from tests.test_runner import NOTHING_TO_PLAY, FakeEngine, runner_of


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(5)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


class TogetherEngine(FakeEngine):
    """Plays a match only once `matches` are being played at once, and then stops the runner; as the first begins, its
    machine's owner sets it to play that many at once."""

    def __init__(self, ladder: LadderWithBots, matches: int) -> None:
        super().__init__()
        self.ladder = ladder
        self.matches_at_once = matches
        self.together = threading.Barrier(matches, timeout=10)
        self.runner: Runner | None = None

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        if len(self.matches) == 0:
            set_machine(self.ladder, matches_at_once=self.matches_at_once)
        self.together.wait()
        assert self.runner is not None
        self.runner.stop()
        return super().play(match, output_folder=output_folder)


def set_machine(ladder: LadderWithBots, **changes: object) -> None:
    """Changes alice's machine `desk`, as she would on her page."""
    response = ladder.server.patch("/api/runners/desk", headers=ladder.alice, json=changes)
    assert response.status_code == 200, response.text


def test_a_runner_plays_more_matches_at_once_as_soon_as_its_owner_says_and_downloads_each_file_once(
    ladder: LadderWithBots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = TogetherEngine(ladder, 2)
    runner = runner_of(ladder, engine, tmp_path, max_matches_at_once=3)
    engine.runner = runner
    downloads: Counter[str] = Counter()
    download = runner.client.download
    asked_naming: list[list[int]] = []
    ask = runner.client.ask

    def counted_download(kind: str, sha256: str, destination: Path) -> None:
        downloads[sha256] += 1
        download(kind, sha256, destination)

    def recorded_ask(**asked: Any) -> dict[str, Any]:
        asked_naming.append(asked["playing"])
        return ask(**asked)

    monkeypatch.setattr(runner.client, "download", counted_download)
    monkeypatch.setattr(runner.client, "ask", recorded_ask)
    runner.run(poll_seconds=0.01)
    played = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert [match["status"] for match in played] == ["finished", "finished"]
    assert set(downloads.values()) == {1}
    first = min(match["id"] for match in played)
    assert [first] in asked_naming, "the ask that drew the second match named the first, being played"


class PausingEngine(FakeEngine):
    """Pauses its machine as its owner would while it plays the first match."""

    def __init__(self, ladder: LadderWithBots) -> None:
        super().__init__()
        self.ladder = ladder

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        set_machine(self.ladder, paused=True)
        return super().play(match, output_folder=output_folder)


def test_a_paused_runner_plays_its_match_to_the_end_and_takes_no_other(ladder: LadderWithBots, tmp_path: Path) -> None:
    runner = runner_of(ladder, PausingEngine(ladder), tmp_path, max_matches_at_once=2)
    stopping = threading.Timer(0.5, runner.stop)
    stopping.start()
    runner.run(poll_seconds=0.01)
    stopping.join()
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert played["status"] == "finished"
    [machine] = ladder.server.get("/api/runners", headers=ladder.alice).json()
    assert (machine["paused"], machine["idle_because"]) == (True, "paused")


class CountingClient:
    """Stands in for the server's client, with nothing to play, keeping each ask."""

    def __init__(self) -> None:
        self.asks: list[dict[str, Any]] = []

    def ask(self, **asked: Any) -> dict[str, Any]:
        self.asks.append(asked)
        return NOTHING_TO_PLAY


def test_outside_its_windows_a_runner_asks_for_no_match_saying_when_the_next_opens_until_stopped(
    tmp_path: Path,
) -> None:
    in_three_days = (datetime.now().weekday() + 3) % 7
    schedule = PlayingSchedule((PlayingWindow(days=frozenset({in_three_days}), start_minute=0, end_minute=1440),))
    client = CountingClient()
    runner = Runner(client, FakeEngine(), cache_folder=tmp_path, work_folder=tmp_path, max_matches_at_once=2)  # type: ignore[arg-type]
    running = threading.Thread(target=runner.run, kwargs={"poll_seconds": 0.01, "schedule": schedule})
    running.start()
    running.join(timeout=0.3)
    runner.stop()
    running.join(timeout=5)
    assert not running.is_alive()
    assert client.asks
    opens = schedule.next_opening(datetime.now())
    assert {asked["next_window_opens"] for asked in client.asks} == {opens}


class StoppingEngine(FakeEngine):
    """Asks the runner to stop while it plays a match, which it then plays to its end."""

    def __init__(self) -> None:
        super().__init__()
        self.runner: Runner | None = None

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        assert self.runner is not None
        self.runner.stop()
        return super().play(match, output_folder=output_folder)


def test_a_runner_asked_to_stop_plays_its_match_to_the_end_and_takes_no_other(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    engine = StoppingEngine()
    runner = runner_of(ladder, engine, tmp_path)
    engine.runner = runner
    runner.run(poll_seconds=0.01)
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert played["status"] == "finished"


class StoppedNowEngine(FakeEngine):
    """Asks the runner to stop now while it plays a match, and is stopped."""

    def __init__(self) -> None:
        super().__init__()
        self.runner: Runner | None = None

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        assert self.runner is not None
        self.runner.stop_now()
        assert self.stopped.wait(timeout=10)
        raise MatchStoppedError("The match was stopped while it was played")


def test_a_runner_stopped_now_stops_its_match_which_is_void(ladder: LadderWithBots, tmp_path: Path) -> None:
    engine = StoppedNowEngine()
    runner = runner_of(ladder, engine, tmp_path)
    engine.runner = runner
    runner.run(poll_seconds=0.01)
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert (played["status"], played["void_because"]) == (
        "void",
        "its runner could not play it: The match was stopped while it was played",
    )


def test_a_match_ready_too_late_to_end_by_its_deadline_is_not_played(ladder: LadderWithBots, tmp_path: Path) -> None:
    app_state = ladder.server.app.state  # type: ignore[attr-defined]
    app_state.config = dataclasses.replace(app_state.config, lease_margin_seconds=-60)
    engine = FakeEngine()
    assert runner_of(ladder, engine, tmp_path).play_next() is False
    assert engine.matches == []
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert played["void_because"] == "its runner could not play it: The match was ready too late to end by its deadline"


def test_a_failure_the_runner_did_not_foresee_voids_its_match_and_stops_it(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    set_machine(ladder, matches_at_once=2)
    runner = runner_of(ladder, FakeEngine(error=RuntimeError("a bug")), tmp_path, max_matches_at_once=2)
    with pytest.raises(ScalaError, match="a bug"):
        runner.run(poll_seconds=0.01)
    played = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    reasons = {match["void_because"] for match in played}
    assert "its runner could not play it: The runner failed: RuntimeError" in reasons
    assert reasons <= {
        "its runner could not play it: The runner failed: RuntimeError",
        "its runner could not play it: The runner was stopped before the match began",
    }


def test_the_cache_keeps_to_its_limit_dropping_the_least_recently_used_first(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    runner = runner_of(ladder, FakeEngine(), tmp_path)
    runner.play_next()
    used = list((tmp_path / "cache").glob("*/*"))
    oldest = tmp_path / "cache" / "bots" / "oldest.zip"
    older = tmp_path / "cache" / "maps" / "older.SC2Map"
    for file, year in ((oldest, 2020), (older, 2021)):
        file.write_bytes(b"x" * 100)
        os.utime(file, (datetime(year, 1, 1).timestamp(), datetime(year, 1, 1).timestamp()))
    runner.cache_bytes = sum(file.stat().st_size for file in used) + 150
    runner.stop()
    runner.run(poll_seconds=0.01)
    assert (oldest.exists(), older.exists()) == (False, True)
    assert all(file.exists() for file in used)


def test_the_latest_kept_match_folders_stay_without_their_bots_and_map(ladder: LadderWithBots, tmp_path: Path) -> None:
    runner = runner_of(ladder, FakeEngine(error=EngineError("Docker is not installed")), tmp_path)
    runner.kept_match_folders = 2
    for _ in range(4):
        runner.play_next()
    played = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    latest = sorted(match["id"] for match in played)[-2:]
    kept = sorted((tmp_path / "work").iterdir())
    assert [folder.name for folder in kept] == [f"match-{id}" for id in latest]
    assert [list(folder.iterdir()) for folder in kept] == [[], []]


class LeftoversStayingEngine(FakeEngine):
    """Cannot remove what a match left, as when Docker is down."""

    def remove_leftovers(self, output_folder: Path) -> None:
        raise EngineError("Docker Compose could not remove the containers of sc2scala-0123456789ab")


def test_a_runner_that_cannot_remove_leftovers_prunes_nothing_and_plays_nothing(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    engine = LeftoversStayingEngine()
    runner = runner_of(ladder, engine, tmp_path)
    runner.kept_match_folders = 0
    (tmp_path / "work" / "match-5" / "output").mkdir(parents=True)
    with pytest.raises(EngineError, match="could not remove"):
        runner.run(poll_seconds=0.01)
    assert (tmp_path / "work" / "match-5").is_dir()
    assert engine.matches == []


def test_a_runner_starting_removes_what_a_killed_runner_left(ladder: LadderWithBots, tmp_path: Path) -> None:
    engine = FakeEngine()
    runner = runner_of(ladder, engine, tmp_path)
    (tmp_path / "work" / "match-5" / "output").mkdir(parents=True)
    partial = tmp_path / "cache" / "bots" / "a.zip.1234abcd.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"half a bot")
    runner.stop()
    runner.run(poll_seconds=0.01)
    assert engine.cleaned_up == [tmp_path / "work" / "match-5" / "output"]
    assert not partial.exists()
