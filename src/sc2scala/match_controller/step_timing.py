from collections.abc import Callable


class StepTiming:
    """How long a bot thinks, as the match controller counts it: from each answer it was sent to its next request.

    Counting starts at `start_counting`. `thinking_seconds` holds the bot's time over all the game, as AI Arena's
    controller counts it for the average step time. A step lasts from the answer to one step request to the next step
    request, and so holds the bot's time alone, not the game's for the requests in between.
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self.thinking_seconds = 0.0
        self._clock = clock
        self._counting = False
        self._answered_at: float | None = None
        self._thinking_since_step = 0.0
        self._has_stepped = False

    def start_counting(self) -> None:
        """Counts the bot's time from now on, as if it had just been answered."""
        self._counting = True
        self._answered_at = self._clock()

    def answered(self) -> None:
        """Marks that the bot was just sent an answer."""
        if self._counting:
            self._answered_at = self._clock()

    def request_received(self) -> None:
        """Adds the time since the bot's last answer to its time."""
        if self._answered_at is None:
            return
        thinking = self._clock() - self._answered_at
        self.thinking_seconds += thinking
        self._thinking_since_step += thinking
        self._answered_at = None

    def step_requested(self) -> float | None:
        """The length of the step the bot just asked to end, in seconds, or `None` for its first, which follows no
        step's answer; called after `request_received`."""
        step_seconds = self._thinking_since_step if self._has_stepped else None
        self._thinking_since_step = 0.0
        self._has_stepped = True
        return step_seconds

    def average_step_seconds(self, game_loop: int) -> float | None:
        """The bot's time over `game_loop`, the game loops it played, as AI Arena's controller averages it, or `None`
        if it played none."""
        return self.thinking_seconds / game_loop if game_loop > 0 else None
