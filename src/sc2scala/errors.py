class ScalaError(Exception):
    """A failure of Scala's own."""


class EngineError(ScalaError, RuntimeError):
    """The engine could not play a match: Docker is missing, a container did not start, or it reported nothing."""


class MatchTimeoutError(EngineError, TimeoutError):
    """A match ran past its real-time limit and a margin, and was stopped."""
