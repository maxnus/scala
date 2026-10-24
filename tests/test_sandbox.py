"""What a bot cannot do in the sandbox, tried by bots that try, against the real engine."""

import json
import os
from pathlib import Path

import pytest

from sc2scala import AiArenaEngine, Bot, Match, MatchResult, Race, ResourceLimits, ResultCode
from tests.bots import assemble_bot

pytestmark = pytest.mark.integration


def play_against_marines(bot: str, tmp_path: Path, map_file: Path, **resource_limits: int) -> MatchResult:
    bots = tmp_path / "bots"
    match = Match(
        player_1=Bot.from_folder(assemble_bot(bot, bots), race=Race.TERRAN),
        player_2=Bot.from_folder(assemble_bot("marine_bot", bots), race=Race.TERRAN),
        map_file=map_file,
        resource_limits=ResourceLimits(**resource_limits),
    )
    return AiArenaEngine().play(match, output_folder=tmp_path / "out")


def test_a_bot_reaches_the_match_controller_alone_and_writes_to_its_data_and_tmp_alone(
    tmp_path: Path, map_file: Path
) -> None:
    result = play_against_marines("sandbox_probe_bot", tmp_path, map_file)
    log = (result.logs_folder / "player_1-sandbox_probe_bot" / "stderr.log").read_text(encoding="utf-8")
    probe = json.loads(next(line for line in log.splitlines() if line.startswith("SANDBOX_PROBE ")).split(" ", 1)[1])
    assert result.code == ResultCode.PLAYER_2_WIN
    assert probe["user"] == os.getuid()
    assert probe["capabilities"] == "0000000000000000"
    assert probe["no_new_privileges"] == "1"
    assert probe["bots visible"] == ["sandbox_probe_bot"]
    for forbidden in [
        "resolve github.com",
        "connect 1.1.1.1:443",
        "resolve sc2_controller",
        "resolve bot_controller2",
        "write /",
        "write its own folder",
    ]:
        assert probe[forbidden].startswith("failed"), forbidden
    for allowed in ["connect proxy_controller:8080", "write its data folder", "write /tmp"]:
        assert probe[allowed] == "succeeded", allowed
    assert probe["processes started of 1100"] < 1024


def test_a_bot_past_its_memory_limit_crashes_and_loses(tmp_path: Path, map_file: Path) -> None:
    result = play_against_marines("memory_hog_bot", tmp_path, map_file, bot_memory_mib=1024)
    assert result.code == ResultCode.PLAYER_1_CRASH


def test_a_bot_whose_logs_grow_past_their_limit_is_stopped_and_loses(tmp_path: Path, map_file: Path) -> None:
    result = play_against_marines("log_flood_bot", tmp_path, map_file, bot_log_mib=10)
    log_folder = result.logs_folder / "player_1-log_flood_bot"
    assert result.code == ResultCode.PLAYER_1_CRASH
    assert sum(path.stat().st_size for path in log_folder.rglob("*") if path.is_file()) < 11 * 1024 * 1024
    assert result.replay_file is not None
