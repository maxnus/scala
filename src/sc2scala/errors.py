class ScalaError(Exception):
    """A failure of Scala's own."""


class EngineError(ScalaError, RuntimeError):
    """The engine could not play a match: Docker is missing, a container did not start, or it reported nothing."""


class MatchTimeoutError(EngineError, TimeoutError):
    """A match ran past its real-time limit and a margin, and was stopped."""


class MatchStoppedError(EngineError):
    """A match was stopped while it was played, its runner stopping."""


class SignInError(ScalaError):
    """Signing in failed: GitHub refused, or the account cannot be used here."""


class ServerError(ScalaError):
    """The server refused a request, or could not be reached."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UploadRefusedError(ScalaError, ValueError):
    """An upload is not what it should be: too large, not a zip or a map, or unsafe to unpack."""


class UploadTooLargeError(UploadRefusedError):
    """An upload holds more than the server takes."""
