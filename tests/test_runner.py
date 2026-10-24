import json
import random
import threading
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from sc2scala import EngineError, Match, MatchResult, ResultCode
from sc2scala.cli import main
from sc2scala.errors import ServerError
from sc2scala.runner import Runner
from sc2scala.scala_client import ScalaClient
from sc2scala.server.models import MatchRecord
from tests.conftest import api_headers_of
from tests.match_helpers import REPLAY_BYTES, LadderWithBots, ladder_with_bots


class FakeEngine:
    """Plays a match by writing what the engine writes: player 1 wins, with a replay and logs; or raises `error`."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.matches: list[Match] = []
        self.stopped = threading.Event()
        self.cleaned_up: list[Path] = []

    def stop_matches(self) -> None:
        self.stopped.set()

    def remove_leftovers(self, output_folder: Path) -> None:
        self.cleaned_up.append(output_folder)

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        self.matches.append(match)
        if self.error is not None:
            raise self.error
        logs = output_folder / "logs"
        for seat, bot in enumerate((match.player_1, match.player_2), start=1):
            (logs / f"player_{seat}-{bot.name}").mkdir(parents=True)
            (logs / f"player_{seat}-{bot.name}" / "stderr.log").write_text(f"{bot.name} played", encoding="utf-8")
        (logs / "proxy_controller").mkdir()
        (logs / "proxy_controller" / "proxy_controller.log").write_text("proxy", encoding="utf-8")
        replay = output_folder / "1_a_vs_b.SC2Replay"
        replay.write_bytes(REPLAY_BYTES)
        return MatchResult(
            code=ResultCode.PLAYER_1_WIN,
            game_steps=2240,
            average_step_times=(0.001, 0.002),
            real_time_seconds=12.0,
            replay_file=replay,
            logs_folder=logs,
        )


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(3)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def runner_of(ladder: LadderWithBots, engine: FakeEngine, tmp_path: Path) -> Runner:
    token = ladder.runner["Authorization"].removeprefix("Bearer ")
    client = ScalaClient("http://localhost", token, http=ladder.server)
    return Runner(client, engine, cache_folder=tmp_path / "cache", work_folder=tmp_path / "work")


def test_a_runner_plays_a_drawn_match_with_its_bots_unpacked_and_reports_it(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    engine = FakeEngine()
    assert runner_of(ladder, engine, tmp_path).play_next() is True
    [match] = engine.matches
    assert {match.player_1.name, match.player_2.name} == {"basic_bot", "bobs_bot"}
    assert match.limits.max_real_time_seconds == 600
    assert match.map_file.name in ("PylonAIE_v4.SC2Map", "TorchesAIE_v4.SC2Map")
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert (played["status"], played["result"], played["game_steps"], played["replay"]) == (
        "finished",
        "Player1Win",
        2240,
        True,
    )
    alices_seat = 1 if played["players"][0]["bot"] == "basic_bot" else 2
    logs = ladder.server.get(f"/api/matches/{played['id']}/logs/{alices_seat}", headers=ladder.alice)
    (tmp_path / "logs.zip").write_bytes(logs.content)
    assert zipfile.ZipFile(tmp_path / "logs.zip").read("stderr.log") == b"basic_bot played"
    engine_logs = ladder.server.get(f"/api/matches/{played['id']}/logs/engine", headers=ladder.alice)
    (tmp_path / "engine.zip").write_bytes(engine_logs.content)
    assert zipfile.ZipFile(tmp_path / "engine.zip").namelist() == ["proxy_controller/proxy_controller.log"]
    assert not (tmp_path / "work" / f"match-{played['id']}").exists()


def test_bots_and_maps_are_downloaded_once(ladder: LadderWithBots, tmp_path: Path) -> None:
    runner = runner_of(ladder, FakeEngine(), tmp_path)
    for _ in range(6):
        runner.play_next()
    assert len(list((tmp_path / "cache" / "bots").iterdir())) == 2
    assert len(list((tmp_path / "cache" / "maps").iterdir())) <= 2


def test_a_match_the_engine_cannot_play_is_reported_void_and_its_folder_kept(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    assert runner_of(ladder, FakeEngine(error=EngineError("Docker is not installed")), tmp_path).play_next() is False
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert (played["status"], played["void_because"]) == (
        "void",
        "its runner could not play it: Docker is not installed",
    )
    assert (tmp_path / "work" / f"match-{played['id']}").is_dir()


def test_a_runner_with_nothing_to_play_says_so(server: TestClient, tmp_path: Path) -> None:
    alice = api_headers_of(server, "alice")
    token = server.post("/api/runners", headers=alice, json={"name": "desk"}).json()["token"]
    runner = Runner(
        ScalaClient("http://localhost", token, http=server),
        FakeEngine(),
        cache_folder=tmp_path / "cache",
        work_folder=tmp_path / "work",
    )
    assert runner.play_next() is False


def test_runner_register_and_start_once(
    ladder: LadderWithBots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for variable in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    token = ladder.alice["Authorization"].removeprefix("Bearer ")
    main(["login", "http://localhost", "--token", token], http=ladder.server)
    assert main(["runner", "register", "laptop", "--ladders", "league", "--no-public"], http=ladder.server) == 0
    assert "This computer is alice/laptop on http://localhost: serves league" in capsys.readouterr().out
    runner_file = tmp_path / "xdg_config_home" / "sc2scala" / "runner.json"
    assert json.loads(runner_file.read_text(encoding="utf-8"))["machine"] == "laptop"
    engine = FakeEngine()
    assert main(["runner", "start", "--once"], http=ladder.server, engine=engine) == 0
    assert len(engine.matches) == 1
    assert main(["runner", "list"], http=ladder.server) == 0
    assert "laptop: serves league, last seen 20" in capsys.readouterr().out


class LateEngine(FakeEngine):
    """Plays past the match's deadline: the server voids it while it plays."""

    def __init__(self, server: TestClient) -> None:
        super().__init__()
        self.server = server

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        with Session(self.server.app.state.engine) as session:  # type: ignore[attr-defined]
            session.execute(update(MatchRecord).values(deadline=datetime(2026, 1, 1).astimezone()))
            session.commit()
        return super().play(match, output_folder=output_folder)


def test_a_result_the_server_refuses_leaves_the_runner_going_and_the_folder_kept(
    ladder: LadderWithBots, tmp_path: Path
) -> None:
    assert runner_of(ladder, LateEngine(ladder.server), tmp_path).play_next() is False
    [played] = ladder.server.get("/api/ladders/league/matches", headers=ladder.alice).json()
    assert (played["status"], played["void_because"]) == ("void", "no result by the deadline")
    assert (tmp_path / "work" / f"match-{played['id']}").is_dir()


class UnreachableAtFirst:
    """Stands in for the server's client: unreachable at the first ask, with nothing to play after, and stops `runner`
    at the `asks_before_stop`th ask."""

    def __init__(self, *, asks_before_stop: int = 0) -> None:
        self.asks = 0
        self.asks_before_stop = asks_before_stop
        self.runner: Runner | None = None

    def next_match(self) -> None:
        self.asks += 1
        if self.runner is not None and self.asks == self.asks_before_stop:
            self.runner.stop()
        if self.asks == 1:
            raise ServerError("http://localhost could not be reached: connection refused")
        return None


def test_a_runner_waits_and_asks_again_when_the_server_cannot_be_reached(tmp_path: Path) -> None:
    client = UnreachableAtFirst(asks_before_stop=3)
    runner = Runner(client, FakeEngine(), cache_folder=tmp_path, work_folder=tmp_path)  # type: ignore[arg-type]
    client.runner = runner
    runner.run(poll_seconds=0.01)
    assert client.asks == 3
    with pytest.raises(ServerError):
        Runner(UnreachableAtFirst(), FakeEngine(), cache_folder=tmp_path, work_folder=tmp_path).play_next()  # type: ignore[arg-type]


def test_runner_set_changes_what_it_is_given_alone(
    ladder: LadderWithBots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    token = ladder.alice["Authorization"].removeprefix("Bearer ")
    main(["login", "http://localhost", "--token", token], http=ladder.server)
    main(["runner", "register", "laptop", "--ladders", "league", "--no-public"], http=ladder.server)
    capsys.readouterr()
    main(["runner", "set", "laptop", "--ladders", "league"], http=ladder.server)
    assert capsys.readouterr().out == "laptop: serves league\n"
    main(["runner", "set", "laptop", "--public"], http=ladder.server)
    assert capsys.readouterr().out == "laptop: serves every public ladder, and league\n"
