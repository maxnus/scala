"""`sc2scala runner start`, `stop` and `service`, run as their user would."""

import os
import random
import signal
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import pytest
from fastapi.testclient import TestClient

from sc2scala import Match, MatchResult, MatchStoppedError
from sc2scala.cli import main
from sc2scala.runner import Runner
from sc2scala.runner_lock import RunnerLock
from tests.match_helpers import LadderWithBots, ladder_with_bots
from tests.test_runner import FakeEngine


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(11)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user's config, cache and data folders, each `tmp_path / <its variable, in lower case>`."""
    for variable in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    return tmp_path


@pytest.fixture
def registered(ladder: LadderWithBots, folders: Path) -> LadderWithBots:
    """`ladder`, with this computer signed in as alice and registered as her machine `laptop`."""
    token = ladder.alice["Authorization"].removeprefix("Bearer ")
    main(["login", "http://localhost", "--token", token], http=ladder.server)
    assert main(["runner", "register", "laptop"], http=ladder.server) == 0
    return ladder


def lock_file(folders: Path) -> Path:
    return folders / "xdg_data_home" / "sc2scala" / "runner.pid"


def test_runner_register_writes_the_machines_settings_once(
    registered: LadderWithBots, folders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = folders / "xdg_config_home" / "sc2scala" / "runner.toml"
    assert "runners = 1" in config_file.read_text(encoding="utf-8")
    config_file.write_text("runners = 3\n", encoding="utf-8")
    capsys.readouterr()
    main(["runner", "register", "study"], http=registered.server)
    assert f"It plays 3 matches at once, at any hour, as {config_file} says" in capsys.readouterr().out
    assert config_file.read_text(encoding="utf-8") == "runners = 3\n"


def test_runner_start_is_refused_while_a_runner_runs(
    registered: LadderWithBots, folders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = FakeEngine()
    with RunnerLock(lock_file(folders)):
        assert main(["runner", "start", "--once"], http=registered.server, engine=engine) == 1
    assert engine.matches == []
    assert main(["runner", "start", "--once"], http=registered.server, engine=engine) == 0
    assert len(engine.matches) == 1


def test_runner_stop_signals_the_runner_to_stop_or_to_stop_now(
    folders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["runner", "stop"]) == 1
    assert capsys.readouterr().out == "No runner is running\n"
    received: list[int] = []

    def receive(signal_number: int, frame: FrameType | None) -> None:
        received.append(signal_number)

    previous = {number: signal.signal(number, receive) for number in (signal.SIGTERM, signal.SIGQUIT)}
    try:
        with RunnerLock(lock_file(folders)):
            assert main(["runner", "stop"]) == 0
            assert main(["runner", "stop", "--now"]) == 0
            deadline = time.monotonic() + 5
            while len(received) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    assert received == [signal.SIGTERM, signal.SIGQUIT]
    assert f"Asked the runner, process {os.getpid()}, to stop once the matches it plays end" in capsys.readouterr().out


class SignalledEngine(FakeEngine):
    """Plays a match while the runner gets SIGTERM `signals` times, and is stopped if a signal stops the runner now."""

    def __init__(self, runners: list[Runner], *, signals: int) -> None:
        super().__init__()
        self.runners = runners
        self.signals = signals

    def play(self, match: Match, *, output_folder: Path) -> MatchResult:
        [runner] = self.runners
        os.kill(os.getpid(), signal.SIGTERM)
        assert _within_seconds(5, lambda: runner.stopping)
        if self.signals == 1:
            return super().play(match, output_folder=output_folder)
        os.kill(os.getpid(), signal.SIGTERM)
        assert self.stopped.wait(timeout=5)
        raise MatchStoppedError("The match was stopped while it was played")


def _within_seconds(seconds: float, condition: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


@pytest.mark.parametrize(("signals", "outcome"), [(1, "finished"), (2, "void")])
def test_a_sigterm_lets_the_match_end_and_a_second_stops_it(
    registered: LadderWithBots, monkeypatch: pytest.MonkeyPatch, signals: int, outcome: str
) -> None:
    runners: list[Runner] = []

    class RecordedRunner(Runner):
        def __init__(self, *args: object, **options: object) -> None:
            super().__init__(*args, **options)  # type: ignore[arg-type]
            runners.append(self)

    monkeypatch.setattr("sc2scala.runner_commands.Runner", RecordedRunner)
    previous = signal.getsignal(signal.SIGTERM)
    engine = SignalledEngine(runners, signals=signals)
    assert main(["runner", "start", "--poll-seconds", "0.01"], http=registered.server, engine=engine) == 0
    assert signal.getsignal(signal.SIGTERM) == previous
    [played] = registered.server.get("/api/ladders/league/matches", headers=registered.alice).json()
    assert played["status"] == outcome


def test_runner_service_writes_a_user_service_that_starts_the_runner(
    folders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["runner", "service"]) == 0
    unit = (folders / "xdg_config_home" / "systemd" / "user" / "sc2scala-runner.service").read_text(encoding="utf-8")
    assert "\nExecStart=" in unit and " -m sc2scala runner start\n" in unit
    assert f"\nEnvironment=XDG_DATA_HOME={folders / 'xdg_data_home'}\n" in unit
    assert "\nKillMode=mixed\n" in unit and "\nWantedBy=default.target\n" in unit
    assert "systemctl --user enable --now sc2scala-runner" in capsys.readouterr().out
