"""Plays one match between two bots, or a bot and the game's built-in computer: creates the game, relays each bot's
requests to its game client and the answers back, and decides how the match ended."""

import asyncio
import contextlib
import logging
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from google.protobuf.message import DecodeError
from s2clientprotocol import common_pb2, sc2api_pb2
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed, WebSocketException

from .controller_config import ComputerConfig, ControllerConfig, SeatConfig
from .step_timing import StepTiming

logger = logging.getLogger(__name__)

TIE = "Tie"
INITIALIZATION_ERROR = "InitializationError"
ERROR = "Error"
"""Outcomes as Scala's `ResultCode` values them, which this package may not import."""

_RACES = {"T": common_pb2.Terran, "Z": common_pb2.Zerg, "P": common_pb2.Protoss, "R": common_pb2.Random}
_SHARED_PORT = 9100
_SERVER_GAME_PORT = 9110
_SERVER_BASE_PORT = 9120
_CLIENT_GAME_PORT = 9130
_CLIENT_BASE_PORT = 9140
"""The ports the two game clients play over, in the game's container, which holds them alone."""
_GAME_CONNECT_RETRY_SECONDS = 0.5
_WEBSOCKET_OPTIONS: dict[str, Any] = {"max_size": None, "compression": None, "ping_interval": None, "close_timeout": 2}
"""No limit on a message's size, since a game's information is large; no compression, which the game does not speak;
no keep-alive pings, which a bot that thinks for seconds without reading could not answer; and 2 seconds, not 10, for
the other side to answer a close, which a bot busy at the game's end may never do."""
_REFUSED_CLOSE_CODE = 1008
_LISTEN_ADDRESS = "0.0.0.0"
"""Every IPv4 address, as Docker's networks have them."""
_LOOKUP_TRIES = 3
_LOOKUP_RETRY_SECONDS = 1.0


def win_of(seat: int) -> str:
    return f"Player{seat}Win"


def crash_of(seat: int) -> str:
    return f"Player{seat}Crash"


def time_out_of(seat: int) -> str:
    return f"Player{seat}TimeOut"


@dataclass(frozen=True)
class ControllerResult:
    """How a match ended: its outcome, the game loops it reached, and each bot's average time for a game loop, in
    seconds, as AI Arena's controller counts it, `None` for a bot that played none and for the computer."""

    outcome: str
    game_steps: int
    average_step_times: tuple[float | None, float | None]

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "game_steps": self.game_steps,
            "average_step_times": list(self.average_step_times),
        }


class _BotFailedError(Exception):
    """A seat's bot broke its connection, sent what is no request, or sent nothing in time."""

    def __init__(self, reason: str, *, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.timed_out = timed_out


class _GameFailedError(Exception):
    """A seat's game client broke its connection, answered with what is no response, did not answer in time, or
    refused what it had to do."""


class MatchController:
    """Plays the match `config` describes, from `play`.

    The first event that decides the match decides it: the game's result as a bot sees it, the game reaching its time,
    a bot that crashes, takes too long, or leaves, or a game client that fails. Before every bot has joined the game,
    any failure is an initialization error. Once decided, the controller gives the other bot up to a request's time to
    reach the game's end, if the game started, saves the replay if no bot's end did, and stops.

    Against the computer, the game has one client, through which the bot plays alone, and the computer neither crashes
    nor runs out of time.
    """

    def __init__(self, config: ControllerConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.clock = clock
        if config.computer is not None:
            self.seats = (_Seat(1, config.seats[0], opponent_name=config.computer.name, controller=self),)
        else:
            self.seats = tuple(
                _Seat(number, seat, opponent_name=config.seats[2 - number].name, controller=self)
                for number, seat in enumerate(config.seats, start=1)
            )
        self.outcome: str | None = None
        self.listening = asyncio.Event()
        """Set once the controller listens for the bots, on `bot_ports`, one for each seat of a bot."""
        self.bot_ports: tuple[int, ...] = ()
        self._outcome_decided = asyncio.Event()
        self._game_created = asyncio.Event()
        self._replay_saved = False
        self._replay_lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        """Whether every bot has joined the game."""
        return all(seat.joined for seat in self.seats)

    async def play(self) -> ControllerResult:
        """Plays the match and returns how it ended, with its replay written, if any game client could save it."""
        servers: list[Server] = []
        background: list[asyncio.Task[None]] = []
        try:
            for seat in self.seats:
                handler = partial(self._serve_bot, seat)
                servers.append(await serve(handler, _LISTEN_ADDRESS, seat.config.listen_port, **_WEBSOCKET_OPTIONS))
            self.bot_ports = tuple(server.sockets[0].getsockname()[1] for server in servers)
            self.listening.set()
            background = [asyncio.create_task(self._start_game()), asyncio.create_task(self._watch_for_bots())]
            await self._outcome_decided.wait()
            await self._let_seats_finish()
            await self._save_replay_from_any_seat()
        finally:
            for task in background:
                task.cancel()
            for server in servers:
                server.close()
                await server.wait_closed()
            for seat in self.seats:
                if seat.game is not None:
                    await seat.game.close()
        assert self.outcome is not None
        step_times = [seat.timing.average_step_seconds(seat.game_loop) for seat in self.seats]
        return ControllerResult(
            outcome=self.outcome,
            game_steps=max(seat.game_loop for seat in self.seats),
            average_step_times=(step_times[0], step_times[1] if len(step_times) == 2 else None),
        )

    def decide(self, outcome: str, reason: str) -> None:
        """Decides the match's `outcome`, for `reason`, unless it was decided already."""
        if self.outcome is not None:
            logger.info(f"Then {reason}")
            return
        logger.info(f"The match ends as {outcome}: {reason}")
        self.outcome = outcome
        self._outcome_decided.set()

    def decide_failure(self, outcome: str, reason: str) -> None:
        """Decides `outcome`, a failure, once every bot has joined the game, and an initialization error before."""
        self.decide(outcome if self.started else INITIALIZATION_ERROR, reason)

    async def wait_for_game(self) -> bool:
        """Waits until the game is created, and returns whether it was, rather than the match decided first."""
        created = asyncio.create_task(self._game_created.wait())
        decided = asyncio.create_task(self._outcome_decided.wait())
        try:
            await asyncio.wait([created, decided], return_when=asyncio.FIRST_COMPLETED)
        finally:
            created.cancel()
            decided.cancel()
        return self._game_created.is_set() and self.outcome is None

    async def save_replay_from(self, seat: "_Seat") -> None:
        """Saves the replay from `seat`'s game client, unless one was saved already; a client that cannot is logged."""
        async with self._replay_lock:
            if self._replay_saved or seat.game is None:
                return
            try:
                response = await seat.ask_game(sc2api_pb2.Request(save_replay=sc2api_pb2.RequestSaveReplay()))
            except _GameFailedError as error:
                logger.warning(f"Seat {seat.number}'s game client saved no replay: {error}")
                return
            if not response.save_replay.data:
                logger.warning(f"Seat {seat.number}'s game client saved no replay: {list(response.error)}")
                return
            try:
                Path(self.config.replay_file).write_bytes(response.save_replay.data)
            except OSError as error:
                logger.warning(f"Could not write the replay to {self.config.replay_file}: {error}")
                return
            self._replay_saved = True
            logger.info(f"Saved the replay from seat {seat.number}'s game client")

    async def _start_game(self) -> None:
        """Connects to each bot's game client and creates the game through the first."""
        timeout = self.config.start_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                for seat in self.seats:
                    seat.game = await _connected_game(seat.config.game_url)
                    await seat.ask_game(sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
                response = await self.seats[0].ask_game(self._create_game_request(), timeout=timeout)
        except TimeoutError:
            self.decide(INITIALIZATION_ERROR, f"the game was not ready within {timeout:g} seconds")
            return
        except _GameFailedError as error:
            self.decide(INITIALIZATION_ERROR, f"the game could not be created: {error}")
            return
        if response.create_game.HasField("error") or response.error:
            details = response.create_game.error_details or list(response.error)
            self.decide(INITIALIZATION_ERROR, f"the game refused to create the match: {details}")
            return
        against = f" against {self.config.computer.name}" if self.config.computer else ""
        logger.info(f"Created the game on {self.config.map_path}{against}")
        self._game_created.set()

    def _create_game_request(self) -> sc2api_pb2.Request:
        participant = sc2api_pb2.PlayerSetup(type=sc2api_pb2.Participant)
        computer = self.config.computer
        return sc2api_pb2.Request(
            create_game=sc2api_pb2.RequestCreateGame(
                local_map=sc2api_pb2.LocalMap(map_path=self.config.map_path),
                player_setup=[participant, _computer_setup(computer) if computer else participant],
                realtime=False,
            )
        )

    async def _watch_for_bots(self) -> None:
        """Decides an initialization error if a bot has not connected in time, as AI Arena's engine does for a bot that
        exits at its start, or never connects."""
        timeout = self.config.connect_timeout_seconds
        deadline = self.clock() + timeout
        while True:
            waiting = [seat for seat in self.seats if seat.bot is None]
            if not waiting:
                return
            if self.clock() > deadline:
                names = " and ".join(seat.config.name for seat in waiting)
                self.decide(INITIALIZATION_ERROR, f"{names} did not connect within {timeout:g} seconds")
                return
            await asyncio.sleep(min(1.0, timeout / 20))

    async def _serve_bot(self, seat: "_Seat", connection: ServerConnection) -> None:
        """Takes the connection of `seat`'s bot, if it is from that bot and the first, and plays the seat through it."""
        peer = connection.remote_address[0] if connection.remote_address else None
        if seat.config.bot_host is not None:
            allowed = await _addresses_of(seat.config.bot_host)
            if peer not in allowed:
                logger.warning(f"Refused a connection to seat {seat.number} from {peer}, which is not its bot")
                await connection.close(_REFUSED_CLOSE_CODE, "not this seat's bot")
                return
        if seat.bot is not None or self.outcome is not None:
            logger.warning(f"Refused another connection to seat {seat.number}, from {peer}")
            await connection.close(_REFUSED_CLOSE_CODE, "the seat is taken")
            return
        logger.info(f"{seat.config.name} connected to seat {seat.number}")
        seat.bot = connection
        seat.task = asyncio.create_task(seat.play())
        await asyncio.wait([seat.task])

    async def _let_seats_finish(self) -> None:
        """Gives the seats still playing a request's time to see their game end, and then stops them; before the game
        started, at once, since a bot that joined waits for one that never will."""
        playing = [seat.task for seat in self.seats if seat.task is not None and not seat.task.done()]
        if not playing:
            return
        grace_seconds = self.config.request_timeout_seconds if self.started else 0
        _, still_playing = await asyncio.wait(playing, timeout=grace_seconds)
        for task in still_playing:
            task.cancel()
        if still_playing:
            await asyncio.wait(still_playing)

    async def _save_replay_from_any_seat(self) -> None:
        for seat in self.seats:
            if not self._replay_saved and seat.game is not None:
                await self.save_replay_from(seat)


class _Seat:
    """A seat of the match: its bot's connection, its game client, and what the controller counts of its game."""

    def __init__(self, number: int, config: SeatConfig, *, opponent_name: str, controller: MatchController) -> None:
        self.number = number
        self.config = config
        self.opponent_name = opponent_name
        self.controller = controller
        self.bot: ServerConnection | None = None
        self.game: ClientConnection | None = None
        self.task: asyncio.Task[None] | None = None
        self.player_id: int | None = None
        self.joined = False
        self.game_loop = 0
        self.strikes = 0
        self.timing = StepTiming(controller.clock)
        self.game_out_of_step = False
        """Whether a request to the game client was cut off before its answer, which then answers the next."""

    @property
    def other_number(self) -> int:
        return 3 - self.number

    @property
    def game_name(self) -> str:
        """The name the seat joins the game under: its bot's, but the second seat's of a bot that plays itself is told
        apart, since the game makes two players who join under one name one player, both controlling player 1 (in game,
        4.10, `docs/plan.md`'s step 8a)."""
        if self.number == 2 and self.config.name == self.opponent_name:
            return f"{self.config.name} (2)"
        return self.config.name

    async def play(self) -> None:
        """Plays the seat until its game ends, or its bot or its game client fails, and decides the match then."""
        if not await self.controller.wait_for_game():
            return
        try:
            await self._join()
            await self._relay()
        except _BotFailedError as failure:
            outcome = time_out_of(self.number) if failure.timed_out else crash_of(self.number)
            self.controller.decide_failure(outcome, f"{self.config.name} {failure}")
            if self.joined:
                await self._end_game_here(leave=True)
        except _GameFailedError as failure:
            self.controller.decide_failure(ERROR, f"seat {self.number}'s game client {failure}")

    async def ask_game(self, request: sc2api_pb2.Request, *, timeout: float | None = None) -> sc2api_pb2.Response:
        """The game client's response to `request`; raises `_GameFailedError` if it gives none in time, or `timeout`."""
        return _parsed_response(await self._ask_game_bytes(request.SerializeToString(), timeout=timeout))

    async def _ask_game_bytes(self, request: bytes, *, timeout: float | None = None) -> bytes:
        if self.game is None:
            raise _GameFailedError("is not connected")
        if self.game_out_of_step:
            raise _GameFailedError("was cut off in a request")
        timeout = timeout or self.controller.config.game_timeout_seconds
        self.game_out_of_step = True
        try:
            async with asyncio.timeout(timeout):
                await self.game.send(request)
                response = await self.game.recv()
            self.game_out_of_step = False
        except TimeoutError:
            raise _GameFailedError(f"did not answer within {timeout:g} seconds") from None
        except ConnectionClosed:
            raise _GameFailedError("closed its connection") from None
        if isinstance(response, str):
            raise _GameFailedError("answered with text")
        return response

    async def _receive_request(self) -> tuple[bytes, sc2api_pb2.Request]:
        """The bot's next request, as it sent it and parsed; raises `_BotFailedError` if it sends none in time."""
        assert self.bot is not None
        timeout = self.controller.config.request_timeout_seconds
        try:
            message = await asyncio.wait_for(self.bot.recv(), timeout)
        except TimeoutError:
            raise _BotFailedError(f"sent nothing for {timeout:g} seconds", timed_out=True) from None
        except ConnectionClosed:
            raise _BotFailedError("closed its connection") from None
        if isinstance(message, str):
            raise _BotFailedError("sent text, not a request")
        try:
            request = sc2api_pb2.Request.FromString(message)
        except DecodeError:
            raise _BotFailedError("sent what is no request") from None
        self.timing.request_received()
        return message, request

    async def _answer(self, response: bytes) -> None:
        """Sends `response` to the bot; raises `_BotFailedError` if it cannot take it in a request's time."""
        assert self.bot is not None
        timeout = self.controller.config.request_timeout_seconds
        try:
            await asyncio.wait_for(self.bot.send(response), timeout)
        except TimeoutError:
            raise _BotFailedError(f"took no answer for {timeout:g} seconds", timed_out=True) from None
        except ConnectionClosed:
            raise _BotFailedError("closed its connection") from None
        self.timing.answered()

    async def _join(self) -> None:
        """Passes the bot's pings on until it asks to join, and its join, as the seat has it, with the match's ports.

        The bot is told its name and its opponent's in the game's information as the match has them, whatever name it
        joined the game under.
        """
        while True:
            message, request = await self._receive_request()
            kind = request.WhichOneof("request")
            if kind == "ping":
                await self._answer(await self._ask_game_bytes(message))
                continue
            if kind != "join_game":
                raise _BotFailedError(f"asked for {kind} before it joined the game")
            timeout = self.controller.config.start_timeout_seconds
            response = await self.ask_game(self._join_request(request), timeout=timeout)
            if response.join_game.HasField("error") or response.error:
                details = response.join_game.error_details or list(response.error)
                raise _GameFailedError(f"refused {self.config.name}'s join: {details}")
            self.player_id = response.join_game.player_id
            await self._answer(response.SerializeToString())
            self.joined = True
            logger.info(f"{self.config.name}, in seat {self.number}, joined the game as player {self.player_id}")
            return

    def _join_request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Request:
        """The bot's join, with the seat's race and name, and the match's ports, its options as asked but for raw
        actions affecting the selection, as AI Arena's controller has them.

        A bot that plays the computer joins with no ports, alone in its game: with ports, the game waits for a second
        player and never starts (in game, 4.10, `docs/plan.md`'s step 8b).
        """
        asked = request.join_game
        join = sc2api_pb2.RequestJoinGame(race=_RACES[self.config.race], player_name=self.game_name)
        if asked.HasField("options"):
            join.options.CopyFrom(asked.options)
            join.options.raw_affects_selection = True
        if self.controller.config.computer is None:
            join.shared_port = _SHARED_PORT
            join.server_ports.game_port = _SERVER_GAME_PORT
            join.server_ports.base_port = _SERVER_BASE_PORT
            join.client_ports.add(game_port=_CLIENT_GAME_PORT, base_port=_CLIENT_BASE_PORT)
        joining = sc2api_pb2.Request(join_game=join)
        if request.HasField("id"):
            joining.id = request.id
        return joining

    async def _relay(self) -> None:
        """Relays the bot's requests and the game's answers until the seat's game ends."""
        config = self.controller.config
        first = True
        while True:
            message, request = await self._receive_request()
            kind = request.WhichOneof("request")
            if kind == "debug":
                await self._answer(_empty_debug_response(request).SerializeToString())
            elif kind in ("leave_game", "quit"):
                await self._leave(request)
                return
            else:
                if kind == "step":
                    self._count_step()
                if kind == "observation" and request.observation.disable_fog:
                    request.observation.disable_fog = False
                    message = request.SerializeToString()
                response = await self._ask_game_bytes(message)
                if kind == "game_info":
                    response = self._named_game_info(_parsed_response(response)).SerializeToString()
                await self._answer(response)
                if kind == "observation" and await self._observed_end(_parsed_response(response), config):
                    return
            if first:
                self.timing.start_counting()
                first = False

    def _count_step(self) -> None:
        """Counts a strike for a step longer than the limit, if one is set; raises `_BotFailedError` at the last."""
        limit_ms = self.controller.config.max_step_time_ms
        step_seconds = self.timing.step_requested()
        if limit_ms is None or step_seconds is None or step_seconds * 1000 <= limit_ms:
            return
        self.strikes += 1
        strikes = self.controller.config.step_time_strikes
        logger.info(f"{self.config.name} took {step_seconds * 1000:.0f} ms for a step: strike {self.strikes}")
        if self.strikes >= strikes:
            raise _BotFailedError(f"took longer than {limit_ms} ms for {strikes} steps", timed_out=True)

    async def _observed_end(self, response: sc2api_pb2.Response, config: ControllerConfig) -> bool:
        """Whether the observation `response` shows the seat's game over, by its result or its time, deciding the match
        if so."""
        observation = response.observation
        self.game_loop = observation.observation.game_loop
        if observation.player_result:
            self.controller.decide(self._outcome_of(observation.player_result), f"{self.config.name}'s game ended")
            await self._end_game_here(leave=False)
            return True
        if self.game_loop > config.max_game_steps:
            self.controller.decide(TIE, f"the game passed {config.max_game_steps} game loops")
            await self._end_game_here(leave=True)
            return True
        return False

    def _outcome_of(self, results: Iterable[sc2api_pb2.PlayerResult]) -> str:
        """The outcome the game's results show, as the seat's own result says, or else its opponent's."""
        by_player = {result.player_id: result.result for result in results}
        own = by_player.get(self.player_id) if self.player_id is not None else None
        if own is None:
            opposed = {sc2api_pb2.Victory: sc2api_pb2.Defeat, sc2api_pb2.Defeat: sc2api_pb2.Victory}
            other = next(iter(by_player.values()))
            own = opposed.get(other, other)
        if own == sc2api_pb2.Victory:
            return win_of(self.number)
        if own == sc2api_pb2.Defeat:
            return win_of(self.other_number)
        return TIE

    def _named_game_info(self, response: sc2api_pb2.Response) -> sc2api_pb2.Response:
        """`response` with both players named, and the opponent's race shown as requested, so that a random opponent
        stays random; the computer's difficulty stays as the game shows it."""
        for player in response.game_info.player_info:
            if player.player_id == self.player_id:
                player.player_name = self.config.name
            else:
                player.player_name = self.opponent_name
                player.race_actual = player.race_requested
        return response

    async def _leave(self, request: sc2api_pb2.Request) -> None:
        """Leaves the game for a bot that asks to leave it or to quit, which loses it the match; a game client is never
        made to quit, so that the replay is saved and the other bot's game ends."""
        self.controller.decide(win_of(self.other_number), f"{self.config.name} left the game")
        await self.controller.save_replay_from(self)
        leave = sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame())
        try:
            response = await self.ask_game(leave)
        except _GameFailedError as error:
            logger.warning(f"Seat {self.number}'s game client could not leave the game: {error}")
            response = sc2api_pb2.Response(leave_game=sc2api_pb2.ResponseLeaveGame(), status=sc2api_pb2.launched)
        if request.WhichOneof("request") == "quit":
            response = sc2api_pb2.Response(quit=sc2api_pb2.ResponseQuit(), status=sc2api_pb2.quit)
        if request.HasField("id"):
            response.id = request.id
        with contextlib.suppress(_BotFailedError):
            await self._answer(response.SerializeToString())

    async def _end_game_here(self, *, leave: bool) -> None:
        """Saves the replay from the seat's game client, unless saved already, and leaves the game, if `leave`."""
        await self.controller.save_replay_from(self)
        if not leave:
            return
        try:
            await self.ask_game(sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame()))
        except _GameFailedError as error:
            logger.warning(f"Seat {self.number}'s game client could not leave the game: {error}")


async def _connected_game(game_url: str) -> ClientConnection:
    """A connection to the game client at `game_url`, tried again until it takes one; the caller bounds the time."""
    while True:
        try:
            return await connect(game_url, **_WEBSOCKET_OPTIONS)
        except (OSError, WebSocketException, TimeoutError):
            await asyncio.sleep(_GAME_CONNECT_RETRY_SECONDS)


async def _addresses_of(host: str) -> set[str]:
    """The addresses `host` resolves to, or none once it failed to resolve `_LOOKUP_TRIES` times, a second apart, so
    that a resolver too busy to answer once does not cost a bot its seat.

    On a match's networks, which reach nothing outside, Docker's resolver answers a name it does not know as it answers
    when it is too busy, with a temporary failure (Docker 29.1, probed in step 8a), so that only failing again tells the
    two apart.
    """
    for attempt in range(1, _LOOKUP_TRIES + 1):
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            if attempt < _LOOKUP_TRIES:
                await asyncio.sleep(_LOOKUP_RETRY_SECONDS)
        else:
            return {str(info[4][0]) for info in infos}
    return set()


def _computer_setup(computer: ComputerConfig) -> sc2api_pb2.PlayerSetup:
    """The computer's seat as the game creates it, its random race and build the game's own pick."""
    return sc2api_pb2.PlayerSetup(
        type=sc2api_pb2.Computer,
        race=_RACES[computer.race],
        difficulty=sc2api_pb2.Difficulty.Value(computer.difficulty),
        ai_build=sc2api_pb2.AIBuild.Value(computer.build),
    )


def _parsed_response(response: bytes) -> sc2api_pb2.Response:
    try:
        return sc2api_pb2.Response.FromString(response)
    except DecodeError:
        raise _GameFailedError("answered with what is no response") from None


def _empty_debug_response(request: sc2api_pb2.Request) -> sc2api_pb2.Response:
    """The answer to a debug request, which never reaches the game, so that no bot cheats."""
    response = sc2api_pb2.Response(debug=sc2api_pb2.ResponseDebug(), status=sc2api_pb2.in_game)
    if request.HasField("id"):
        response.id = request.id
    return response
