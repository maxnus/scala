import asyncio
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from s2clientprotocol import common_pb2, sc2api_pb2

import sc2scala.match_controller
import sc2scala.match_controller.match_controller as match_controller_module
from sc2scala import ResultCode
from sc2scala.computer import Build, Difficulty
from sc2scala.match_controller.controller_config import (
    BUILDS,
    DIFFICULTIES,
    ComputerConfig,
    ControllerConfig,
    SeatConfig,
)
from sc2scala.match_controller.match_controller import (
    ERROR,
    INITIALIZATION_ERROR,
    TIE,
    ControllerResult,
    MatchController,
    crash_of,
    time_out_of,
    win_of,
)
from sc2scala.match_controller.step_timing import StepTiming
from tests.fake_game import FakeBot, FakeGame

_MATCH_SECONDS = 15
_COMPUTER = ComputerConfig("Computer-VeryHard-Zerg-Rush", "Z", "VeryHard", "Rush")


def _config(tmp_path: Path, urls: tuple[str, str], **changes: object) -> ControllerConfig:
    """A config for the fake game at `urls`, with `changes`; seats given among them take those addresses."""
    if "seats" in changes:
        seats = changes["seats"]
        assert isinstance(seats, tuple)
        changes["seats"] = tuple(
            replace(seat, game_url=url) for seat, url in zip(seats, urls[: len(seats)], strict=True)
        )
    config = ControllerConfig(
        map_path="/maps/Fake.SC2Map",
        seats=(SeatConfig("alpha", "T", urls[0], 0), SeatConfig("beta", "Z", urls[1], 0)),
        replay_file=str(tmp_path / "match.SC2Replay"),
        result_file=str(tmp_path / "result.json"),
        max_game_steps=10_000,
        request_timeout_seconds=2.0,
        game_timeout_seconds=4.0,
        connect_timeout_seconds=4.0,
        start_timeout_seconds=4.0,
    )
    return replace(config, **changes)  # type: ignore[arg-type]


def _play(tmp_path: Path, game: FakeGame, bots: tuple[FakeBot | None, ...], **changes: object) -> ControllerResult:
    """Plays a match through the fake game between `bots`, leaving out a seat's bot for `None`."""

    tmp_path.mkdir(parents=True, exist_ok=True)

    async def playing() -> ControllerResult:
        urls = await game.start()
        controller = MatchController(_config(tmp_path, urls, **changes))
        try:
            match = asyncio.create_task(controller.play())
            await controller.listening.wait()
            bot_tasks = [
                asyncio.create_task(bot.play(port))
                for bot, port in zip(bots, controller.bot_ports, strict=True)
                if bot is not None
            ]
            result = await asyncio.wait_for(match, _MATCH_SECONDS)
            for task in bot_tasks:
                task.cancel()
            await asyncio.gather(*bot_tasks, return_exceptions=True)
            return result
        finally:
            await asyncio.wait_for(game.stop(), _MATCH_SECONDS)

    return asyncio.run(playing())


def _play_computer(tmp_path: Path, game: FakeGame, bot: FakeBot | None, **changes: object) -> ControllerResult:
    """Plays a match through the fake game between `bot`, or no bot for `None`, and the computer."""
    computer = changes.pop("computer", _COMPUTER)
    seats = (SeatConfig("alpha", "T", "", 0),)
    return _play(tmp_path, game, (bot,), seats=seats, computer=computer, **changes)


def test_a_game_played_to_its_end_has_the_result_the_game_gives(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=80)
    alpha, beta = FakeBot(), FakeBot()
    result = _play(tmp_path, game, (alpha, beta))
    assert result.outcome == win_of(1)
    assert result.game_steps == 80
    assert all(step_time is not None and step_time >= 0 for step_time in result.average_step_times)
    assert (alpha.result, beta.result) == (sc2api_pb2.Victory, sc2api_pb2.Defeat)
    assert (tmp_path / "match.SC2Replay").read_bytes().startswith(b"replay from client")
    assert not game.requests_of("leave_game")


@pytest.mark.parametrize(
    ("results", "outcome"),
    [((sc2api_pb2.Defeat, sc2api_pb2.Victory), win_of(2)), ((sc2api_pb2.Tie, sc2api_pb2.Tie), TIE)],
)
def test_a_defeat_and_a_tie_are_the_games(
    tmp_path: Path, results: tuple[sc2api_pb2.Result.ValueType, sc2api_pb2.Result.ValueType], outcome: str
) -> None:
    result = _play(tmp_path, FakeGame(ends_at_loop=40, results=results), (FakeBot(), FakeBot()))
    assert result.outcome == outcome


def test_a_game_past_its_time_is_a_tie_and_saves_its_replay(tmp_path: Path) -> None:
    game = FakeGame()
    result = _play(tmp_path, game, (FakeBot(), FakeBot()), max_game_steps=40)
    assert result.outcome == TIE
    assert result.game_steps > 40
    assert game.requests_of("leave_game") and (tmp_path / "match.SC2Replay").is_file()


def test_a_bot_that_closes_its_connection_crashes_and_its_game_client_leaves(tmp_path: Path) -> None:
    game = FakeGame()
    alpha, beta = FakeBot(ends_after_steps=3, ending="crash"), FakeBot()
    result = _play(tmp_path, game, (alpha, beta))
    assert result.outcome == crash_of(1)
    assert game.requests_of("leave_game", client=1)
    assert game.requests_of("save_replay", client=1)
    assert beta.result == sc2api_pb2.Victory


def test_a_bot_that_sends_nothing_for_a_request_s_time_loses_by_a_time_out(tmp_path: Path) -> None:
    beta = FakeBot(ends_after_steps=2, ending="silence")
    result = _play(tmp_path, FakeGame(), (FakeBot(), beta), request_timeout_seconds=0.5)
    assert result.outcome == time_out_of(2)


@pytest.mark.parametrize("ending", ["leave", "quit"])
def test_a_bot_that_leaves_or_quits_loses_and_its_game_client_only_leaves(tmp_path: Path, ending: str) -> None:
    game = FakeGame()
    alpha = FakeBot(ends_after_steps=2, ending=ending)
    result = _play(tmp_path, game, (alpha, FakeBot()))
    assert result.outcome == win_of(2)
    assert game.requests_of("leave_game", client=1) and not game.requests_of("quit")
    kind = "quit" if ending == "quit" else "leave_game"
    assert alpha.responses[-1].WhichOneof("response") == kind
    if ending == "quit":
        assert alpha.responses[-1].id == 9


def test_debug_requests_are_answered_empty_and_never_reach_the_game(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=40)
    alpha = FakeBot(cheats=True)
    _play(tmp_path, game, (alpha, FakeBot()))
    debug_answers = [response for response in alpha.responses if response.HasField("debug")]
    assert debug_answers and all(response.id >= 100 for response in debug_answers)
    assert not game.requests_of("debug")


def test_the_join_takes_the_seats_race_and_name_and_the_matchs_ports(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=16)
    alpha, beta = FakeBot(race=common_pb2.Protoss), FakeBot(race=common_pb2.Random)
    _play(tmp_path, game, (alpha, beta))
    joins = game.joins
    assert (joins[1].race, joins[2].race) == (common_pb2.Terran, common_pb2.Zerg)
    assert (joins[1].player_name, joins[2].player_name) == ("alpha", "beta")
    assert joins[1].server_ports == joins[2].server_ports
    assert joins[1].client_ports == joins[2].client_ports
    assert joins[1].server_ports.game_port not in (1, 3) and len(joins[1].client_ports) == 1
    assert joins[1].options.raw and joins[1].options.score and joins[1].options.raw_affects_selection
    assert game.requests_of("join_game", client=1)[0].id == 7
    assert alpha.player_id == 1 and beta.player_id == 2


def test_a_bot_that_plays_itself_joins_the_second_seat_under_another_name_but_is_told_its_own(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=16)
    beta = FakeBot()
    seats = (SeatConfig("mirror", "T", "", 0), SeatConfig("mirror", "Z", "", 0))
    _play(tmp_path, game, (FakeBot(), beta), seats=seats)
    assert (game.joins[1].player_name, game.joins[2].player_name) == ("mirror", "mirror (2)")
    game_info = next(response.game_info for response in beta.responses if response.HasField("game_info"))
    assert {player.player_name for player in game_info.player_info} == {"mirror"}


def test_the_game_info_names_both_players_and_shows_the_opponent_s_race_as_requested(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=16, actual_races=(common_pb2.Terran, common_pb2.Protoss))
    alpha = FakeBot()
    seats = (SeatConfig("alpha", "T", "", 0), SeatConfig("beta", "R", "", 0))
    _play(tmp_path, game, (alpha, FakeBot()), seats=seats)
    game_info = next(response.game_info for response in alpha.responses if response.HasField("game_info"))
    own, opponent = sorted(game_info.player_info, key=lambda player: player.player_id)
    assert (own.player_name, own.race_actual) == ("alpha", common_pb2.Terran)
    assert (opponent.player_name, opponent.race_actual) == ("beta", common_pb2.Random)


def test_an_observation_never_lifts_the_fog(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=16)
    _play(tmp_path, game, (FakeBot(lifts_fog=True), FakeBot()))
    observations = game.requests_of("observation", client=1)
    assert observations and not any(request.observation.disable_fog for request in observations)


def test_slow_steps_lose_by_a_time_out_only_where_the_ladder_sets_a_limit(tmp_path: Path) -> None:
    def slow() -> FakeBot:
        return FakeBot(think_seconds=0.06)

    unlimited = _play(tmp_path / "unlimited", FakeGame(ends_at_loop=48), (slow(), FakeBot()))
    assert unlimited.outcome == win_of(1)
    limited = _play(
        tmp_path / "limited", FakeGame(ends_at_loop=400), (slow(), FakeBot()), max_step_time_ms=30, step_time_strikes=3
    )
    assert limited.outcome == time_out_of(1)


def test_neither_the_first_step_nor_the_game_s_time_counts_against_the_limit(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=48, observation_seconds=0.06)
    alpha = FakeBot(start_seconds=0.3)
    result = _play(tmp_path, game, (alpha, FakeBot()), max_step_time_ms=30, step_time_strikes=1)
    assert result.outcome == win_of(1)


def test_a_bot_that_never_connects_is_an_initialization_error_and_the_other_is_not_kept_waiting(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _play(tmp_path, FakeGame(), (FakeBot(), None), connect_timeout_seconds=1.0, request_timeout_seconds=10.0)
    assert result.outcome == INITIALIZATION_ERROR
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("before_joining", ["crash", "wait"])
def test_a_bot_that_crashes_or_waits_before_joining_is_an_initialization_error(
    tmp_path: Path, before_joining: str
) -> None:
    beta = FakeBot(before_joining=before_joining)
    result = _play(tmp_path, FakeGame(), (FakeBot(), beta), request_timeout_seconds=0.5)
    assert result.outcome == INITIALIZATION_ERROR


def test_a_game_that_refuses_to_create_the_match_is_an_initialization_error(tmp_path: Path) -> None:
    result = _play(tmp_path, FakeGame(refuses_to_create=True), (FakeBot(), FakeBot()))
    assert result.outcome == INITIALIZATION_ERROR


def test_a_game_client_that_fails_during_the_game_is_an_error(tmp_path: Path) -> None:
    game = FakeGame(failing_client=2)
    result = _play(tmp_path, game, (FakeBot(), FakeBot()))
    assert result.outcome == ERROR


def test_a_connection_from_any_address_but_the_seat_s_bot_s_is_refused(tmp_path: Path) -> None:
    seats = (SeatConfig("alpha", "T", "", 0, bot_host="127.0.0.2"), SeatConfig("beta", "Z", "", 0))
    alpha = FakeBot()
    result = _play(tmp_path, FakeGame(), (alpha, FakeBot()), seats=seats, connect_timeout_seconds=1.0)
    assert result.outcome == INITIALIZATION_ERROR
    assert alpha.closed_by_controller and not alpha.responses


def test_a_connection_from_the_seat_s_bot_s_address_is_taken(tmp_path: Path) -> None:
    seats = (SeatConfig("alpha", "T", "", 0, bot_host="localhost"), SeatConfig("beta", "Z", "", 0, "127.0.0.1"))
    result = _play(tmp_path, FakeGame(ends_at_loop=16), (FakeBot(), FakeBot()), seats=seats)
    assert result.outcome == win_of(1)


def test_a_bot_plays_the_computer_alone_in_a_game_created_with_it_and_joins_with_no_ports(tmp_path: Path) -> None:
    game = FakeGame(ends_at_loop=80)
    alpha = FakeBot()
    result = _play_computer(tmp_path, game, alpha)
    assert result.outcome == win_of(1)
    assert result.game_steps == 80
    assert result.average_step_times[0] is not None and result.average_step_times[1] is None
    computer = game.requests_of("create_game")[0].create_game.player_setup[1]
    assert (computer.type, computer.race) == (sc2api_pb2.Computer, common_pb2.Zerg)
    assert (computer.difficulty, computer.ai_build) == (sc2api_pb2.VeryHard, sc2api_pb2.Rush)
    join = game.joins[1]
    assert not join.HasField("shared_port") and not join.HasField("server_ports") and not join.client_ports
    assert alpha.player_id == 1 and not [client for client, _ in game.requests if client == 2]
    assert (tmp_path / "match.SC2Replay").is_file()


@pytest.mark.parametrize(
    ("results", "outcome"),
    [((sc2api_pb2.Defeat, sc2api_pb2.Victory), win_of(2)), ((sc2api_pb2.Tie, sc2api_pb2.Tie), TIE)],
)
def test_the_computer_wins_or_ties_as_the_game_says(
    tmp_path: Path, results: tuple[sc2api_pb2.Result.ValueType, sc2api_pb2.Result.ValueType], outcome: str
) -> None:
    assert _play_computer(tmp_path, FakeGame(ends_at_loop=40, results=results), FakeBot()).outcome == outcome


@pytest.mark.parametrize(
    ("ending", "outcome"), [("leave", win_of(2)), ("crash", crash_of(1)), ("silence", time_out_of(1))]
)
def test_a_bot_that_leaves_crashes_or_falls_silent_against_the_computer_loses_to_it(
    tmp_path: Path, ending: str, outcome: str
) -> None:
    bot = FakeBot(ends_after_steps=2, ending=ending)
    assert _play_computer(tmp_path, FakeGame(), bot, request_timeout_seconds=0.5).outcome == outcome


def test_a_bot_that_never_connects_to_play_the_computer_is_an_initialization_error(tmp_path: Path) -> None:
    result = _play_computer(tmp_path, FakeGame(), None, connect_timeout_seconds=0.5)
    assert result.outcome == INITIALIZATION_ERROR


def test_the_game_info_names_the_computer_and_keeps_its_random_race_and_its_difficulty(tmp_path: Path) -> None:
    alpha = FakeBot()
    computer = ComputerConfig("Computer-Hard-Random-RandomBuild", "R", "Hard", "RandomBuild")
    _play_computer(tmp_path, FakeGame(ends_at_loop=16), alpha, computer=computer)
    game_info = next(response.game_info for response in alpha.responses if response.HasField("game_info"))
    own, opponent = sorted(game_info.player_info, key=lambda player: player.player_id)
    assert own.player_name == "alpha"
    assert (opponent.player_name, opponent.type) == ("Computer-Hard-Random-RandomBuild", sc2api_pb2.Computer)
    assert (opponent.race_actual, opponent.difficulty) == (common_pb2.Random, sc2api_pb2.Hard)


def test_every_outcome_is_a_result_code() -> None:
    outcomes = [TIE, INITIALIZATION_ERROR, ERROR] + [
        outcome(seat) for outcome in (win_of, crash_of, time_out_of) for seat in (1, 2)
    ]
    assert {ResultCode(outcome) for outcome in outcomes} == set(ResultCode)


def test_a_step_lasts_from_a_step_s_answer_to_the_next_step_request_and_holds_the_bot_s_time_alone() -> None:
    now = [0.0]
    timing = StepTiming(lambda: now[0])
    timing.request_received()
    timing.answered()
    now[0] = 5.0
    timing.start_counting()
    step_lengths = []
    for bot_seconds, is_step in [(0.010, False), (0.002, True), (0.003, False), (0.004, True)]:
        now[0] += bot_seconds
        timing.request_received()
        if is_step:
            step_lengths.append(timing.step_requested())
        now[0] += 1.0
        timing.answered()
    assert step_lengths == [None, pytest.approx(0.007)]
    assert timing.thinking_seconds == pytest.approx(0.019)
    assert timing.average_step_seconds(19) == pytest.approx(0.001)
    assert timing.average_step_seconds(0) is None


def test_the_config_reads_back_as_written(tmp_path: Path) -> None:
    urls = ("ws://game:5001/sc2api", "ws://game:5002/sc2api")
    config = _config(tmp_path, urls, max_step_time_ms=40)
    assert ControllerConfig.from_json(config.to_json()) == config
    against_computer = _config(tmp_path, urls, seats=(SeatConfig("alpha", "T", "", 0),), computer=_COMPUTER)
    assert ControllerConfig.from_json(against_computer.to_json()) == against_computer
    with pytest.raises(ValueError):
        SeatConfig("alpha", "X", "", 0)
    with pytest.raises(ValueError):
        _config(tmp_path, urls, computer=_COMPUTER)
    with pytest.raises(ValueError):
        _config(tmp_path, urls, seats=(SeatConfig("alpha", "T", "", 0),))


def test_a_computer_s_difficulty_and_build_are_the_protocol_s_and_scala_s() -> None:
    assert set(DIFFICULTIES) == set(sc2api_pb2.Difficulty.keys()) == {difficulty.value for difficulty in Difficulty}
    assert set(BUILDS) == set(sc2api_pb2.AIBuild.keys()) == {build.value for build in Build}
    for wrong in ({"difficulty": "VeryHrd"}, {"build": "Turtle"}, {"race": "Terran"}):
        with pytest.raises(ValueError):
            replace(_COMPUTER, **wrong)


def test_the_controller_runs_on_its_own_with_nothing_else_of_scala_s(tmp_path: Path) -> None:
    shutil.copytree(Path(sc2scala.match_controller.__file__).parent, tmp_path / "match_controller")
    blocking_scala = (
        "import runpy, sys; sys.modules['sc2scala'] = None; runpy.run_module('match_controller', run_name='__main__')"
    )
    finished = subprocess.run(
        [sys.executable, "-c", blocking_scala], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert finished.returncode == 2, finished.stderr
    assert "Usage: python -m match_controller CONFIG" in finished.stderr


def test_a_name_that_fails_to_resolve_is_tried_again_before_it_counts_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures = {"busy": 2, "gone": 3}

    async def getaddrinfo(self: object, host: str, port: object, **options: object) -> list[tuple[object, ...]]:
        if failures[host]:
            failures[host] -= 1
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 0))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(match_controller_module, "_LOOKUP_RETRY_SECONDS", 0.0)
    assert asyncio.run(match_controller_module._addresses_of("busy")) == {"10.0.0.2"}
    assert asyncio.run(match_controller_module._addresses_of("gone")) == set()
