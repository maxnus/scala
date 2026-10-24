from enum import StrEnum


class ResultCode(StrEnum):
    """How a match ended, valued as the engine reports it."""

    PLAYER_1_WIN = "Player1Win"
    PLAYER_2_WIN = "Player2Win"
    TIE = "Tie"
    """A draw, or a game that reached its game time."""
    PLAYER_1_CRASH = "Player1Crash"
    PLAYER_2_CRASH = "Player2Crash"
    PLAYER_1_TIME_OUT = "Player1TimeOut"
    """Player 1 took too long for its steps."""
    PLAYER_2_TIME_OUT = "Player2TimeOut"
    INITIALIZATION_ERROR = "InitializationError"
    """A bot or the game did not start."""
    ERROR = "Error"
