"""What `bot remove` and `ladder remove` share: asking at the terminal first, and saying what the removal did."""

import sys
from typing import Any

from sc2scala.errors import ScalaError


def confirm_at_terminal(question: str, *, assume_yes: bool) -> None:
    """Asks `question` at the terminal, and raises `ScalaError` unless the answer is yes; asks nothing if `assume_yes`.

    Without a terminal to ask at, it raises unless `assume_yes`, which the command's `--yes` gives. Ctrl-D or Ctrl-C at
    the question answers no.
    """
    if assume_yes:
        return
    if sys.stdin is None or not sys.stdin.isatty():
        raise ScalaError(f"{question} No terminal is here to answer: give --yes to go on")
    try:
        answer = input(f"{question} [y/N] ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        raise ScalaError("Stopped: nothing was changed")


def removal_line(removed: dict[str, Any], *, drawn: str, stays: str) -> str:
    """What removing a bot or a ladder did, as the server's answer says: `drawn` says how it came to its matches."""
    if removed["outcome"] == "deleted":
        return f"Deleted {removed['name']}"
    matches = removed["matches"]
    return f"Retired {removed['name']}, which {drawn} {matches} {'match' if matches == 1 else 'matches'}: {stays}"
