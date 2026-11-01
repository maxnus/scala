"""A fake game and fake bots, for playing Scala's match controller with no StarCraft II and no Docker."""

import asyncio
from dataclasses import dataclass, field
from functools import partial

from s2clientprotocol import common_pb2, sc2api_pb2
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

FAKE_GAME_VERSION = "4.10.0"


class FakeGame:
    """The two game clients of one game, each a websocket server on this machine, answering as much of the protocol
    as the controller and the fake bots ask for.

    Player 1 plays through the first client and player 2 through the second. As in a real game, the clients step in
    lockstep: a step returns once the other client has asked to step as far, or has left or failed. A game created
    with the computer as player 2 has one participant, through the first client, whose steps wait for nobody, and its
    game information shows the computer's race as requested and its difficulty, but no build, as the real game's
    does (4.10). With
    `ends_at_loop`, the game ends once a client's loop reaches it, with `results` for each player; a player that leaves
    loses, and the other wins at its next observation. `observation_seconds` delays each answer to an observation.
    With `failing_client`, that client closes its connection at its first step.
    """

    def __init__(
        self,
        *,
        ends_at_loop: int | None = None,
        results: tuple[sc2api_pb2.Result.ValueType, sc2api_pb2.Result.ValueType] = (
            sc2api_pb2.Victory,
            sc2api_pb2.Defeat,
        ),
        actual_races: tuple[common_pb2.Race.ValueType, common_pb2.Race.ValueType] = (
            common_pb2.Terran,
            common_pb2.Protoss,
        ),
        refuses_to_create: bool = False,
        observation_seconds: float = 0.0,
        failing_client: int | None = None,
    ) -> None:
        self.ends_at_loop = ends_at_loop
        self.results = results
        self.actual_races = actual_races
        self.refuses_to_create = refuses_to_create
        self.observation_seconds = observation_seconds
        self.failing_client = failing_client
        self.requests: list[tuple[int, sc2api_pb2.Request]] = []
        """Each request either client was sent, with the client's number, 1 or 2."""
        self.joins: dict[int, sc2api_pb2.RequestJoinGame] = {}
        self.computer: sc2api_pb2.PlayerSetup | None = None
        """The computer's seat, if the game was created with one."""
        self.left: set[int] = set()
        self.game_loops = {1: 0, 2: 0}
        self._failed: set[int] = set()
        self._stepped = asyncio.Condition()
        self._handlers: set[asyncio.Task[object]] = set()
        self._both_joined = asyncio.Event()
        self._servers: list[Server] = []

    async def start(self) -> tuple[str, str]:
        """Starts both clients and returns their addresses."""
        for client in (1, 2):
            handler = partial(self._serve, client)
            self._servers.append(await serve(handler, "127.0.0.1", 0))
        first, second = (f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/sc2api" for server in self._servers)
        return first, second

    async def stop(self) -> None:
        """Stops both clients, and what they were answering."""
        for handler in self._handlers:
            handler.cancel()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    def requests_of(self, kind: str, *, client: int | None = None) -> list[sc2api_pb2.Request]:
        """The requests of `kind` the clients were sent, or only `client`."""
        return [
            request
            for number, request in self.requests
            if request.WhichOneof("request") == kind and client in (None, number)
        ]

    async def _serve(self, client: int, connection: ServerConnection) -> None:
        handler = asyncio.current_task()
        assert handler is not None
        self._handlers.add(handler)
        try:
            async for message in connection:
                assert isinstance(message, bytes)
                request = sc2api_pb2.Request.FromString(message)
                self.requests.append((client, request))
                if request.WhichOneof("request") == "step" and client == self.failing_client:
                    await self._mark(self._failed, client)
                    await connection.close()
                    return
                response = await self._answer(client, request)
                if request.HasField("id"):
                    response.id = request.id
                await connection.send(response.SerializeToString())
        except ConnectionClosed:
            pass

    async def _answer(self, client: int, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        kind = request.WhichOneof("request")
        if kind == "ping":
            return sc2api_pb2.Response(ping=sc2api_pb2.ResponsePing(game_version=FAKE_GAME_VERSION))
        if kind == "create_game":
            if self.refuses_to_create:
                error = sc2api_pb2.ResponseCreateGame.InvalidMapPath
                return sc2api_pb2.Response(create_game=sc2api_pb2.ResponseCreateGame(error=error))
            computers = [setup for setup in request.create_game.player_setup if setup.type == sc2api_pb2.Computer]
            self.computer = computers[0] if computers else None
            return sc2api_pb2.Response(create_game=sc2api_pb2.ResponseCreateGame(), status=sc2api_pb2.init_game)
        if kind == "join_game":
            self.joins[client] = request.join_game
            if len(self.joins) == (1 if self.computer else 2):
                self._both_joined.set()
            await self._both_joined.wait()
            join = sc2api_pb2.ResponseJoinGame(player_id=client)
            return sc2api_pb2.Response(join_game=join, status=sc2api_pb2.in_game)
        if kind == "game_info":
            return sc2api_pb2.Response(game_info=self._game_info())
        if kind == "step":
            await self._step(client, request.step.count or 1)
            return sc2api_pb2.Response(step=sc2api_pb2.ResponseStep(simulation_loop=self.game_loops[client]))
        if kind == "observation":
            await asyncio.sleep(self.observation_seconds)
            return sc2api_pb2.Response(observation=self._observation(client))
        if kind == "action":
            return sc2api_pb2.Response(action=sc2api_pb2.ResponseAction())
        if kind == "leave_game":
            await self._mark(self.left, client)
            return sc2api_pb2.Response(leave_game=sc2api_pb2.ResponseLeaveGame(), status=sc2api_pb2.launched)
        if kind == "save_replay":
            replay = f"replay from client {client}".encode()
            return sc2api_pb2.Response(save_replay=sc2api_pb2.ResponseSaveReplay(data=replay))
        return sc2api_pb2.Response(error=[f"The fake game does not answer {kind}"])

    async def _step(self, client: int, loops: int) -> None:
        other = 3 - client
        async with self._stepped:
            self.game_loops[client] += loops
            self._stepped.notify_all()
            await self._stepped.wait_for(
                lambda: (
                    self.computer is not None
                    or self.game_loops[other] >= self.game_loops[client]
                    or other in self.left | self._failed
                )
            )

    async def _mark(self, clients: set[int], client: int) -> None:
        async with self._stepped:
            clients.add(client)
            self._stepped.notify_all()

    def _game_info(self) -> sc2api_pb2.ResponseGameInfo:
        players = [
            sc2api_pb2.PlayerInfo(
                player_id=client,
                type=sc2api_pb2.Participant,
                race_requested=self.joins[client].race if client in self.joins else common_pb2.NoRace,
                race_actual=self.actual_races[client - 1],
            )
            for client in ((1,) if self.computer else (1, 2))
        ]
        if self.computer is not None:
            players.append(
                sc2api_pb2.PlayerInfo(
                    player_id=2,
                    type=sc2api_pb2.Computer,
                    race_requested=self.computer.race,
                    difficulty=self.computer.difficulty,
                )
            )
        return sc2api_pb2.ResponseGameInfo(map_name="Fake", player_info=players)

    def _observation(self, client: int) -> sc2api_pb2.ResponseObservation:
        game_loop = self.game_loops[client]
        observation = sc2api_pb2.ResponseObservation(observation=sc2api_pb2.Observation(game_loop=game_loop))
        if self.left:
            for player in (1, 2):
                result = sc2api_pb2.Defeat if player in self.left else sc2api_pb2.Victory
                observation.player_result.add(player_id=player, result=result)
        elif self.ends_at_loop is not None and self.game_loops[client] >= self.ends_at_loop:
            for player in (1, 2):
                observation.player_result.add(player_id=player, result=self.results[player - 1])
        return observation


@dataclass
class FakeBot:
    """A bot that joins, steps the game `step_loops` at a time until it ends, and acts as told on its way.

    After `ends_after_steps` steps, it does as `ending` says: `crash` closes its connection, `silence` sends nothing
    more, `leave` and `quit` ask so. `think_seconds` is how long it waits before each step after its first, and
    `start_seconds` before its first. It asks to lift the fog with `lifts_fog`, and sends a debug request each step
    with `cheats`. `before_joining` stops it before it joins: `crash` or `wait`.
    """

    race: common_pb2.Race.ValueType = common_pb2.Random
    step_loops: int = 8
    ends_after_steps: int | None = None
    ending: str = "crash"
    think_seconds: float = 0.0
    start_seconds: float = 0.0
    lifts_fog: bool = False
    cheats: bool = False
    before_joining: str | None = None
    responses: list[sc2api_pb2.Response] = field(default_factory=list)
    """Every response the bot was sent."""
    closed_by_controller: bool = False

    @property
    def result(self) -> int | None:
        """The bot's result, as the last observation it saw has it, or `None`."""
        for response in reversed(self.responses):
            for result in response.observation.player_result:
                if result.player_id == self.player_id:
                    return result.result
        return None

    @property
    def player_id(self) -> int | None:
        joins = [response.join_game.player_id for response in self.responses if response.HasField("join_game")]
        return joins[0] if joins else None

    async def play(self, port: int) -> None:
        try:
            async with connect(f"ws://127.0.0.1:{port}/sc2api", max_size=None, ping_interval=None) as connection:
                await self._play(connection)
        except ConnectionClosed:
            self.closed_by_controller = True

    async def _ask(self, connection: ClientConnection, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        await connection.send(request.SerializeToString())
        message = await connection.recv()
        assert isinstance(message, bytes)
        response = sc2api_pb2.Response.FromString(message)
        self.responses.append(response)
        return response

    async def _play(self, connection: ClientConnection) -> None:
        if self.before_joining == "crash":
            return
        if self.before_joining == "wait":
            await asyncio.sleep(3600)
        await self._ask(connection, sc2api_pb2.Request(ping=sc2api_pb2.RequestPing()))
        join = sc2api_pb2.RequestJoinGame(
            race=self.race,
            options=sc2api_pb2.InterfaceOptions(raw=True, score=True),
            server_ports=sc2api_pb2.PortSet(game_port=1, base_port=2),
            client_ports=[sc2api_pb2.PortSet(game_port=3, base_port=4)],
        )
        await self._ask(connection, sc2api_pb2.Request(join_game=join, id=7))
        await self._ask(connection, sc2api_pb2.Request(game_info=sc2api_pb2.RequestGameInfo()))
        steps = 0
        while True:
            observing = sc2api_pb2.RequestObservation(disable_fog=self.lifts_fog)
            observed = await self._ask(connection, sc2api_pb2.Request(observation=observing))
            if observed.observation.player_result:
                return
            if self.ends_after_steps is not None and steps >= self.ends_after_steps:
                await self._end(connection)
                return
            await self._ask(connection, sc2api_pb2.Request(action=sc2api_pb2.RequestAction()))
            if self.cheats:
                await self._ask(connection, sc2api_pb2.Request(debug=sc2api_pb2.RequestDebug(), id=steps + 100))
            await asyncio.sleep(self.think_seconds if steps else self.start_seconds)
            await self._ask(connection, sc2api_pb2.Request(step=sc2api_pb2.RequestStep(count=self.step_loops)))
            steps += 1

    async def _end(self, connection: ClientConnection) -> None:
        if self.ending == "silence":
            await asyncio.sleep(3600)
        elif self.ending == "leave":
            await self._ask(connection, sc2api_pb2.Request(leave_game=sc2api_pb2.RequestLeaveGame()))
        elif self.ending == "quit":
            await self._ask(connection, sc2api_pb2.Request(quit=sc2api_pb2.RequestQuit(), id=9))
