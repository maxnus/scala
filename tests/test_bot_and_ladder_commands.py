import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc2scala.cli import main
from tests.bot_folders import make_python_bot
from tests.conftest import MAP_BYTES, api_headers_of


@pytest.fixture
def signed_in(server: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The test server, with the command signed in to it as alice."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    token = api_headers_of(server, "alice")["Authorization"].removeprefix("Bearer ")
    assert main(["login", "http://localhost", "--token", token], http=server) == 0
    return server


@pytest.fixture
def map_files(tmp_path: Path) -> list[Path]:
    paths = [tmp_path / "PylonAIE_v4.SC2Map", tmp_path / "TorchesAIE_v4.SC2Map"]
    for number, path in enumerate(paths):
        path.write_bytes(MAP_BYTES + bytes([number]))
    return paths


def test_a_bots_folder_is_uploaded_as_a_zip_without_caches(
    signed_in: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_python_bot(tmp_path / "bots", "basic_bot")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "bot.cpython-312.pyc").write_bytes(b"cache")
    (folder / "data").mkdir()
    (folder / "data" / "openings.json").write_text("{}", encoding="utf-8")
    assert main(["bot", "upload", "basic_bot", str(folder), "--race", "terran"], http=signed_in) == 0
    assert "basic_bot: version 1, a python bot" in capsys.readouterr().out
    archive = signed_in.get("/api/bots/basic_bot/versions/1/archive", headers=api_headers_of(signed_in, "alice"))
    zip_path = tmp_path / "downloaded.zip"
    zip_path.write_bytes(archive.content)
    assert sorted(zipfile.ZipFile(zip_path).namelist()) == ["data/openings.json", "run.py"]


def test_a_bot_is_listed_and_shown(signed_in: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["bot", "upload", "basic_bot", str(make_python_bot(tmp_path, "basic_bot")), "--race", "Z"], http=signed_in)
    capsys.readouterr()
    assert main(["bot", "list"], http=signed_in) == 0
    assert capsys.readouterr().out == "basic_bot: Z, by alice, version 1\n"
    assert main(["bot", "show", "basic_bot"], http=signed_in) == 0
    assert "  version 1: python" in capsys.readouterr().out


def test_a_refused_upload_fails_the_command(signed_in: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "no_entry_point"
    folder.mkdir()
    (folder / "bot.py").write_text("", encoding="utf-8")
    assert main(["bot", "upload", "no_entry_point", str(folder), "--race", "T"], http=signed_in) == 1


def test_a_ladder_is_made_entered_and_shown(
    signed_in: TestClient, tmp_path: Path, map_files: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    command = ["ladder", "create", "league", "--maps", *map(str, map_files), "--limit", "bot_memory_mib=2048"]
    assert main(command, http=signed_in) == 0
    main(["bot", "upload", "basic_bot", str(make_python_bot(tmp_path, "basic_bot")), "--race", "T"], http=signed_in)
    assert main(["ladder", "enter", "league", "basic_bot"], http=signed_in) == 0
    capsys.readouterr()
    assert main(["ladder", "show", "league"], http=signed_in) == 0
    shown = capsys.readouterr().out
    assert "league: public, by alice, 1 bots on 2 maps" in shown
    assert "Maps: PylonAIE_v4, TorchesAIE_v4" in shown
    assert "bot_memory_mib=2048" in shown
    assert "  basic_bot, by alice, version 1" in shown


def test_a_protected_ladder_asks_for_its_password(
    signed_in: TestClient,
    map_files: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("getpass.getpass", lambda prompt: "open sesame")
    assert main(["ladder", "create", "secret", "--maps", str(map_files[0]), "--protect"], http=signed_in) == 0
    bob = api_headers_of(signed_in, "bob")
    assert signed_in.get("/api/ladders/secret", headers=bob).status_code == 403
    bobs_token = bob["Authorization"].removeprefix("Bearer ")
    main(["login", "http://localhost", "--token", bobs_token], http=signed_in)
    assert main(["ladder", "join", "secret"], http=signed_in) == 0
    assert "secret: protected, by alice, 0 bots on 1 maps, you are a member" in capsys.readouterr().out


def test_a_limit_must_be_name_equals_value(signed_in: TestClient, map_files: list[Path]) -> None:
    with pytest.raises(SystemExit):
        main(["ladder", "create", "league", "--maps", str(map_files[0]), "--limit", "bot_memory_mib"], http=signed_in)
