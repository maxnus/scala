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
        """The type of the bot in `folder`, named for the bot, by the first entry point it holds.

        The entry points are tried in the order the members are listed, and a binary named for the bot reads as
        `CPP_LINUX`. Raises `ValueError` if the folder holds none.
        """
        name = folder.name
        entry_points = [
            ("run.py", cls.PYTHON),
            (f"{name}.dll", cls.DOTNET_CORE),
            (f"{name}.jar", cls.JAVA),
            (f"{name}.js", cls.NODEJS),
            (f"{name}.exe", cls.CPP_WIN32),
            (name, cls.CPP_LINUX),
        ]
        for file_name, bot_type in entry_points:
            if (folder / file_name).is_file():
                return bot_type
        expected = ", ".join(file_name for file_name, _ in entry_points)
        raise ValueError(f"{folder} holds no entry point for a bot named {name!r}: one of {expected}")
