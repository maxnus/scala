import threading
from pathlib import Path

from sc2scala.log_watchdog import LogWatchdog


def make_log_folders(tmp_path: Path) -> list[Path]:
    folders = [tmp_path / "bot_controller1", tmp_path / "bot_controller2"]
    for folder in folders:
        (folder / "bot").mkdir(parents=True)
    return folders


def test_logs_within_their_limit_stop_nothing(tmp_path: Path) -> None:
    folders = make_log_folders(tmp_path)
    (folders[0] / "bot" / "stderr.log").write_bytes(b"x" * 100)
    stops: list[int] = []
    with LogWatchdog(folders, max_bytes=1000, stop_bot=stops.append, check_interval_seconds=0.01):
        threading.Event().wait(0.1)
    assert stops == []


def test_the_bot_whose_logs_grow_too_large_is_stopped_once(tmp_path: Path) -> None:
    folders = make_log_folders(tmp_path)
    stopped = threading.Event()
    stops: list[int] = []

    def stop_bot(seat: int) -> None:
        stops.append(seat)
        stopped.set()

    with LogWatchdog(folders, max_bytes=1000, stop_bot=stop_bot, check_interval_seconds=0.01) as watchdog:
        (folders[1] / "bot" / "a.log").write_bytes(b"x" * 600)
        (folders[1] / "bot" / "b.log").write_bytes(b"x" * 600)
        assert stopped.wait(5)
        threading.Event().wait(0.05)
    assert watchdog.exceeded_seat == 2
    assert stops == [2]


def test_the_stopped_bots_logs_are_cut_to_the_limit(tmp_path: Path) -> None:
    folders = make_log_folders(tmp_path)
    flood = folders[0] / "bot" / "stderr.log"
    other_bot_log = folders[1] / "bot" / "stderr.log"
    other_bot_log.write_bytes(b"y" * 5000)
    with LogWatchdog(folders, max_bytes=1000, stop_bot=lambda seat: None, check_interval_seconds=0.01) as watchdog:
        flood.write_bytes(b"x" * 5000)
        while watchdog.exceeded_seat is None:
            threading.Event().wait(0.01)
    assert flood.read_bytes().startswith(b"x" * 1000 + b"\n[Scala cut this log at 1000 bytes")
    assert other_bot_log.stat().st_size == 5000
