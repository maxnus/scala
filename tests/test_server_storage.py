"""The file store, the bot zip's checks, the passwords and the limits, apart from the routes that use them."""

import io
from pathlib import Path

import pytest

from sc2scala.errors import UploadRefusedError, UploadTooLargeError
from sc2scala.server.file_store import FileStore
from sc2scala.server.ladder_limits import LimitRefusedError, limits_changed
from sc2scala.server.passwords import hash_of_password, password_matches
from sc2scala.server.upload_checks import type_of_bot_archive
from tests.conftest import zip_of


def accept(path: Path) -> str:
    return "accepted"


def refuse(path: Path) -> str:
    raise UploadRefusedError("refused")


def files_in(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


def test_a_file_stored_twice_is_kept_once_under_its_hash(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    first, result = store.store(io.BytesIO(b"content"), kind="bots", suffix=".zip", max_bytes=100, check=accept)
    second, _ = store.store(io.BytesIO(b"content"), kind="bots", suffix=".zip", max_bytes=100, check=accept)
    assert result == "accepted"
    assert first == second
    assert files_in(tmp_path / "bots") == [f"{first.sha256}.zip"]
    assert first.path.read_bytes() == b"content"


def test_a_file_too_large_or_refused_is_not_kept(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    with pytest.raises(UploadTooLargeError):
        store.store(io.BytesIO(b"x" * 101), kind="bots", suffix=".zip", max_bytes=100, check=accept)
    with pytest.raises(UploadRefusedError, match="refused"):
        store.store(io.BytesIO(b"x"), kind="bots", suffix=".zip", max_bytes=100, check=refuse)
    assert files_in(tmp_path / "bots") == []


def test_a_zip_that_unpacks_to_too_much_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "bot.zip"
    archive.write_bytes(zip_of({"run.py": b"", "model.bin": b"\0" * 2000}))
    with pytest.raises(UploadRefusedError, match="unpacks to more"):
        type_of_bot_archive(archive, bot_name="bot", max_unpacked_bytes=1000)


def test_a_password_matches_its_hash_alone(tmp_path: Path) -> None:
    password_hash = hash_of_password("open sesame")
    assert password_hash.startswith("scrypt$")
    assert password_matches("open sesame", password_hash)
    assert not password_matches("open sesame!", password_hash)
    assert hash_of_password("open sesame") != password_hash


def test_limits_are_changed_by_name_and_kept_whole_where_whole() -> None:
    game, resource = limits_changed(
        {"max_game_steps": "40320", "bot_cpus": 1.5}, game_limits={}, resource_limits={"bot_memory_mib": 2048}
    )
    assert (game["max_game_steps"], game["max_step_time_ms"]) == (40320, 40)
    assert (resource["bot_cpus"], resource["bot_memory_mib"], resource["game_cpus"]) == (1.5, 2048, 4.0)
    for refused in [{"cores": 1}, {"bot_cpus": 0}, {"bot_processes": 10.5}, {"bot_log_mib": "lots"}]:
        with pytest.raises(LimitRefusedError):
            limits_changed(refused, game_limits={}, resource_limits={})
