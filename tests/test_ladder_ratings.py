import random
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sc2scala.cli import main
from tests.conftest import zip_of
from tests.match_helpers import LadderWithBots, draw, ladder_with_bots, report
from tests.test_bots_api import upload


@pytest.fixture
def ladder(server: TestClient) -> LadderWithBots:
    server.app.state.match_random = random.Random(11)  # type: ignore[attr-defined]
    return ladder_with_bots(server)


def play(ladder: LadderWithBots, *, winner: str | None, code: str | None = None) -> dict[str, Any]:
    """Draws a match and reports `winner`'s win, or `code` as it is."""
    match = draw(ladder.server, ladder.runner)
    assert match is not None
    if code is None:
        seat = next(player["seat"] for player in match["players"] if player["bot"] == winner)
        code = f"Player{seat}Win"
    report(ladder.server, ladder.runner, match["id"], code, with_files=False)
    return match


def standings_of(ladder: LadderWithBots) -> dict[str, dict[str, Any]]:
    listed = ladder.server.get("/api/ladders/league/standings", headers=ladder.alice).json()
    return {standing["bot"]: standing for standing in listed}


def test_bots_are_unrated_before_their_first_counted_game(ladder: LadderWithBots) -> None:
    play(ladder, winner=None, code="InitializationError")
    standings = standings_of(ladder)
    assert [standings[bot]["rating"] for bot in ("basic_bot", "bobs_bot")] == [None, None]


def test_the_winner_is_rated_above_the_loser_about_the_mean(ladder: LadderWithBots) -> None:
    for _ in range(3):
        play(ladder, winner="basic_bot")
    play(ladder, winner="bobs_bot")
    standings = standings_of(ladder)
    first, second = ladder.server.get("/api/ladders/league/standings", headers=ladder.alice).json()
    assert (first["bot"], second["bot"]) == ("basic_bot", "bobs_bot")
    assert first["rating"] > 1500 > second["rating"]
    assert first["rating"] - 1500 == pytest.approx(1500 - second["rating"], abs=1)
    assert (standings["basic_bot"]["wins"], standings["basic_bot"]["losses"], standings["basic_bot"]["games"]) == (
        3,
        1,
        4,
    )
    assert standings["basic_bot"]["interval"] > 0


def test_a_crash_counts_as_a_loss_and_a_tie_as_half(ladder: LadderWithBots) -> None:
    match = play(ladder, winner=None, code="Player1Crash")
    crashed = match["players"][0]["bot"]
    play(ladder, winner=None, code="Tie")
    standings = standings_of(ladder)
    assert (standings[crashed]["losses"], standings[crashed]["ties"], standings[crashed]["crashes"]) == (1, 1, 1)
    survivor = next(bot for bot in standings if bot != crashed)
    assert (standings[survivor]["wins"], standings[survivor]["crashes"]) == (1, 0)


def test_a_new_version_starts_unrated(ladder: LadderWithBots) -> None:
    play(ladder, winner="basic_bot")
    upload(ladder.server, ladder.alice, "basic_bot", zip_of({"run.py": b"print('v2')"}))
    standings = standings_of(ladder)
    assert (standings["basic_bot"]["version"], standings["basic_bot"]["rating"]) == (2, None)
    assert standings["bobs_bot"]["rating"] is not None


def test_the_standings_command_lists_the_bots_by_rating(
    ladder: LadderWithBots, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    play(ladder, winner="bobs_bot")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    main(
        ["login", "http://localhost", "--token", ladder.alice["Authorization"].removeprefix("Bearer ")],
        http=ladder.server,
    )
    capsys.readouterr()
    assert main(["ladder", "standings", "league"], http=ladder.server) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("  1. bobs_bot (Z, v1, by bob): 15")
    assert "1 games, 1-0-0, 0 crashes" in lines[0]
    assert lines[1].startswith("  2. basic_bot (T, v1, by alice): 14")
