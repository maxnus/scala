import sys

from sc2scala.errors import ScalaError


def confirm_at_terminal(question: str, *, assume_yes: bool) -> None:
    """Asks `question` at the terminal, and raises `ScalaError` unless the answer is yes; asks nothing if `assume_yes`.

    Without a terminal to ask at, it raises unless `assume_yes`, which the command's `--yes` gives.
    """
    if assume_yes:
        return
    if sys.stdin is None or not sys.stdin.isatty():
        raise ScalaError(f"{question} No terminal is here to answer: give --yes to go on")
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        raise ScalaError("Stopped: nothing was changed")
