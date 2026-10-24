from pathlib import Path

import pytest

from sc2scala import BotType


@pytest.mark.parametrize(
    ("entry_point", "bot_type"),
    [
        ("run.py", BotType.PYTHON),
        ("MyBot.dll", BotType.DOTNET_CORE),
        ("MyBot.jar", BotType.JAVA),
        ("MyBot.js", BotType.NODEJS),
        ("MyBot.exe", BotType.CPP_WIN32),
        ("MyBot", BotType.CPP_LINUX),
    ],
)
def test_a_bot_type_is_read_from_its_entry_point(tmp_path: Path, entry_point: str, bot_type: BotType) -> None:
    folder = tmp_path / "MyBot"
    folder.mkdir()
    (folder / entry_point).write_bytes(b"")
    assert BotType.from_folder(folder) == bot_type


def test_the_first_entry_point_listed_wins(tmp_path: Path) -> None:
    folder = tmp_path / "MyBot"
    folder.mkdir()
    (folder / "MyBot.exe").write_bytes(b"")
    (folder / "run.py").write_bytes(b"")
    assert BotType.from_folder(folder) == BotType.PYTHON


def test_an_entry_point_is_named_for_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "MyBot"
    folder.mkdir()
    (folder / "OtherBot.jar").write_bytes(b"")
    with pytest.raises(ValueError, match="No entry point"):
        BotType.from_folder(folder)


def test_a_folder_named_like_the_bot_is_no_entry_point(tmp_path: Path) -> None:
    folder = tmp_path / "MyBot"
    (folder / "MyBot").mkdir(parents=True)
    with pytest.raises(ValueError, match="No entry point"):
        BotType.from_folder(folder)


def test_a_bot_type_is_read_from_the_names_of_its_files() -> None:
    assert BotType.from_file_names({"MyBot.jar", "data/model.bin"}, bot_name="MyBot") == BotType.JAVA
