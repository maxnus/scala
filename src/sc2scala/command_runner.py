import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sc2scala.errors import EngineError


class CommandRunner(Protocol):
    """Runs a command to its end, its output appended to `log_file`, and returns its exit code.

    The command runs in a process group of its own, so that a Ctrl-C in the terminal reaches the program that started
    it alone, which then decides what becomes of it.

    Raises `subprocess.TimeoutExpired` once `timeout` seconds have passed, the command stopped.
    """

    def __call__(self, args: Sequence[str], *, timeout: float | None, log_file: Path) -> int: ...


def run_command(args: Sequence[str], *, timeout: float | None, log_file: Path) -> int:
    """Runs `args` as a `CommandRunner` does, and raises `EngineError` if its program is not installed."""
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"$ {shlex.join(args)}\n")
        log.flush()
        try:
            completed = subprocess.run(
                args, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False, process_group=0
            )
        except FileNotFoundError as error:
            raise EngineError(f"{args[0]} is not installed") from error
    return completed.returncode
