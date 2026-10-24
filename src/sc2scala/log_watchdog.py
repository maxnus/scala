import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType


class LogWatchdog:
    """Watches each bot's log folder while a match plays, and stops the bot whose folder holds more than `max_bytes`.

    Used as a context manager around the match. The folders are given in seat order. `stop_bot` is called from the
    watchdog's own thread with the seat, 1 or 2, of the bot whose logs grew too large, which `exceeded_seat` then
    holds, and again every `retry_interval_seconds` while that bot's logs still grow. On leaving the context, the files
    in that bot's folder are cut to `max_bytes` each, ended by a note.
    """

    def __init__(
        self,
        bot_log_folders: Sequence[Path],
        *,
        max_bytes: int,
        stop_bot: Callable[[int], object],
        check_interval_seconds: float = 0.1,
        retry_interval_seconds: float = 2.0,
    ) -> None:
        self.bot_log_folders = list(bot_log_folders)
        self.max_bytes = max_bytes
        self.check_interval_seconds = check_interval_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self.exceeded_seat: int | None = None
        self._stop_bot = stop_bot
        self._match_over = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="log-watchdog", daemon=True)

    def __enter__(self) -> "LogWatchdog":
        self._thread.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._match_over.set()
        self._thread.join()
        if self.exceeded_seat is not None:
            _cut_files_in(self.bot_log_folders[self.exceeded_seat - 1], max_bytes=self.max_bytes)

    def _watch(self) -> None:
        while not self._match_over.wait(self.check_interval_seconds):
            for seat, folder in enumerate(self.bot_log_folders, start=1):
                if _bytes_in(folder) > self.max_bytes:
                    self.exceeded_seat = seat
                    self._stop_until_its_logs_stop_growing(seat, folder)
                    return

    def _stop_until_its_logs_stop_growing(self, seat: int, folder: Path) -> None:
        bytes_when_stopped = _bytes_in(folder)
        self._stop_bot(seat)
        while not self._match_over.wait(self.retry_interval_seconds):
            bytes_now = _bytes_in(folder)
            if bytes_now <= bytes_when_stopped:
                return
            bytes_when_stopped = bytes_now
            self._stop_bot(seat)


def _bytes_in(folder: Path) -> int:
    """The size of the files in `folder` and below, those that vanish while it is read left out."""
    total = 0
    for parent, _, file_names in os.walk(folder):
        for file_name in file_names:
            try:
                total += os.stat(os.path.join(parent, file_name)).st_size
            except OSError:
                continue
    return total


def _cut_files_in(folder: Path, *, max_bytes: int) -> None:
    """Cuts each file in `folder` and below that holds more than `max_bytes` to that size, and ends it with a note."""
    note = f"\n[Scala cut this log at {max_bytes} bytes: the bot's logs grew past their limit]\n".encode()
    for parent, _, file_names in os.walk(folder):
        for file_name in file_names:
            path = Path(parent) / file_name
            if path.stat().st_size > max_bytes:
                with path.open("r+b") as log:
                    log.truncate(max_bytes)
                    log.seek(0, os.SEEK_END)
                    log.write(note)
