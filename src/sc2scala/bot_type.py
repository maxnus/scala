from collections.abc import Collection
from enum import StrEnum
from pathlib import Path


class BotType(StrEnum):
    """How the engine starts a bot, valued as AI Arena names it."""

    PYTHON = "python"
    """`python run.py`"""
    DOTNET_CORE = "dotnetcore"
    """`dotnet <name>.dll`"""
    JAVA = "java"
    """`java -jar <name>.jar`"""
    NODEJS = "nodejs"
    """`node <name>.js`"""
    CPP_WIN32 = "cppwin32"
    """`wine <name>.exe`, which AI Arena's v0.8.0 bot image cannot run: it holds no Wine."""
    CPP_LINUX = "cpplinux"
    """`./<name>`"""
    LINUX = "linux"
    """`./<name>`, as `CPP_LINUX`"""

    @classmethod
    def from_folder(cls, folder: Path) -> "BotType":
        """The type of the bot in `folder`, named for the bot, as `from_file_names` reads it from the files there.

        Raises `ValueError` if the folder holds no entry point.
        """
        file_names = {path.name for path in folder.iterdir() if path.is_file()} if folder.is_dir() else set()
        try:
            return cls.from_file_names(file_names, bot_name=folder.name)
        except ValueError as error:
            raise ValueError(f"{folder}: {error}") from None

    @classmethod
    def from_file_names(cls, file_names: Collection[str], *, bot_name: str) -> "BotType":
        """The type of the bot `bot_name` whose folder holds `file_names`, by the first entry point among them.

        The entry points are tried in the order the members are listed, and a binary named for the bot reads as
        `CPP_LINUX`. Raises `ValueError` if there is none.
        """
        entry_points = [
            ("run.py", cls.PYTHON),
            (f"{bot_name}.dll", cls.DOTNET_CORE),
            (f"{bot_name}.jar", cls.JAVA),
            (f"{bot_name}.js", cls.NODEJS),
            (f"{bot_name}.exe", cls.CPP_WIN32),
            (bot_name, cls.CPP_LINUX),
        ]
        for file_name, bot_type in entry_points:
            if file_name in file_names:
                return bot_type
        expected = ", ".join(file_name for file_name, _ in entry_points)
        raise ValueError(f"No entry point for a bot named {bot_name!r}: one of {expected}")
