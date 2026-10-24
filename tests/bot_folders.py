from pathlib import Path


def make_python_bot(parent: Path, name: str) -> Path:
    """A folder named `name` in `parent`, holding a Python bot's entry point."""
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "run.py").write_text("print('a bot')\n", encoding="utf-8")
    return folder


def make_map(parent: Path, name: str = "TestMapLE") -> Path:
    """An `.SC2Map` file named `name` in `parent`."""
    map_file = parent / f"{name}.SC2Map"
    map_file.write_bytes(b"a map")
    return map_file
