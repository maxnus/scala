import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from sc2scala.bot_archive import type_of_bot_archive, unpack_bot_archive
from sc2scala.bot_type import BotType
from sc2scala.errors import UploadRefusedError
from tests.conftest import zip_of


def write_zip(tmp_path: Path, content: bytes) -> Path:
    archive = tmp_path / "bot.zip"
    archive.write_bytes(content)
    return archive


def test_a_zip_is_unpacked_into_its_folder_the_dot_slash_names_too(tmp_path: Path) -> None:
    archive = write_zip(tmp_path, zip_of({"./run.py": b"print()", "data/model.bin": b"weights"}))
    unpack_bot_archive(archive, tmp_path / "bot", max_unpacked_bytes=1000)
    assert (tmp_path / "bot" / "run.py").read_bytes() == b"print()"
    assert (tmp_path / "bot" / "data" / "model.bin").read_bytes() == b"weights"


def test_an_executable_stays_executable(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        binary = zipfile.ZipInfo("bot")
        binary.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(binary, b"\x7fELF")
    unpack_bot_archive(write_zip(tmp_path, buffer.getvalue()), tmp_path / "bot", max_unpacked_bytes=1000)
    assert os.access(tmp_path / "bot" / "bot", os.X_OK)


@pytest.mark.parametrize(
    "files",
    [{"../escape.py": b""}, {"/etc/passwd": b""}, {"run.py": b"", "big.bin": b"\0" * 5000}],
    ids=["a path out of the folder", "an absolute path", "more than the limit"],
)
def test_an_unsafe_zip_writes_nothing_outside_its_folder_and_leaves_no_folder(tmp_path: Path, files: dict) -> None:
    archive = write_zip(tmp_path, zip_of(files))
    with pytest.raises(UploadRefusedError):
        unpack_bot_archive(archive, tmp_path / "unpacked" / "bot", max_unpacked_bytes=1000)
    assert not (tmp_path / "unpacked" / "bot").exists()
    assert not (tmp_path / "unpacked" / "escape.py").exists()


def test_a_zip_of_the_bots_folder_itself_is_read_and_unpacked_as_that_folder(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path,
        zip_of(
            {
                "MyBot/run.py": b"print()",
                "MyBot/data/model.bin": b"weights",
                "__MACOSX/MyBot/._run.py": b"attributes",
            }
        ),
    )
    assert type_of_bot_archive(archive, bot_name="MyBot", max_unpacked_bytes=1000) == BotType.PYTHON
    unpack_bot_archive(archive, tmp_path / "bot", max_unpacked_bytes=1000)
    assert sorted(path.relative_to(tmp_path / "bot").as_posix() for path in (tmp_path / "bot").rglob("*")) == [
        "data",
        "data/model.bin",
        "run.py",
    ]


def test_macos_attributes_beside_a_bot_at_the_zips_root_are_passed_over(tmp_path: Path) -> None:
    archive = write_zip(tmp_path, zip_of({"run.py": b"print()", "__MACOSX/._run.py": b"attributes"}))
    unpack_bot_archive(archive, tmp_path / "bot", max_unpacked_bytes=1000)
    assert [path.name for path in (tmp_path / "bot").iterdir()] == ["run.py"]
