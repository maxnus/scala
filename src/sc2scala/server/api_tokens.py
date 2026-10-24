import hashlib
import secrets

TOKEN_PREFIX = "scala_"
RUNNER_TOKEN_PREFIX = "scala_runner_"


def new_token() -> str:
    """A token for the CLI or a runner: random, and marked as Scala's by its prefix."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def new_runner_token() -> str:
    """A token for a runner machine, marked as one by its prefix."""
    return RUNNER_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_of_token(token: str) -> str:
    """The hash under which the server keeps `token`: its SHA-256, unsalted."""
    return hashlib.sha256(token.encode()).hexdigest()
