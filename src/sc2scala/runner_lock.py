"""The lock that lets one `sc2scala runner start` run per user, and tells `sc2scala runner stop` which process it is."""

import fcntl
import os
from pathlib import Path
from types import TracebackType

from sc2scala.errors import ScalaError


class RunnerRunningError(ScalaError):
    """A runner is running already, as this user."""


class RunnerLock:
    """A file holding the running runner's process id, which it keeps locked while it runs.

    The system lets the lock go when the process ends, however it ends, so a lock file left behind holds no one.
    """

    def __init__(self, file: Path) -> None:
        self.file = file
        self._descriptor: int | None = None

    def __enter__(self) -> "RunnerLock":
        """Takes the lock, and raises `RunnerRunningError` if another process holds it."""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            running = running_runner(self.file)
            raise RunnerRunningError(
                f"A runner is running already{f', as process {running}' if running else ''}: "
                "stop it with `sc2scala runner stop`"
            ) from None
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        self._descriptor = descriptor
        return self

    def __exit__(
        self, error_type: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


def running_runner(file: Path) -> int | None:
    """The process id of the runner that holds the lock in `file`, or `None` if none does."""
    try:
        descriptor = os.open(file, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            text = os.read(descriptor, 32).decode().strip()
            return int(text) if text.isdigit() else None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return None
    finally:
        os.close(descriptor)
