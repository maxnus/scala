import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from sc2scala.cli import main
from sc2scala.server.database import create_database_engine, upgrade_database
from sc2scala.server.models import Base
from sc2scala.server.server_config import CONFIG_FILE_NAME, ServerConfig
from tests.conftest import server_config_in


def test_a_new_data_folder_gets_a_config_for_this_machine_alone_and_a_secret_key(tmp_path: Path) -> None:
    config = ServerConfig.from_data_folder(tmp_path / "data")
    assert (config.host, config.port, config.public_url, config.dev_sign_in) == (
        "127.0.0.1",
        8000,
        "http://127.0.0.1:8000",
        True,
    )
    assert not config.github_sign_in
    assert (tmp_path / "data" / CONFIG_FILE_NAME).is_file()
    key_file = tmp_path / "data" / "secret_key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert ServerConfig.from_data_folder(tmp_path / "data").secret_key == config.secret_key


def test_the_config_file_is_read(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'host = "0.0.0.0"\nport = 9000\npublic_url = "https://scala.example.org/"\ndev_sign_in = false\n'
        '[github]\nclient_id = "id"\nclient_secret = "secret"\n',
        encoding="utf-8",
    )
    config = ServerConfig.from_data_folder(tmp_path)
    assert (config.host, config.port, config.public_url) == ("0.0.0.0", 9000, "https://scala.example.org")
    assert config.github_sign_in
    assert config.keep_logs_days == 30


def test_logs_are_kept_the_days_the_config_says_and_never_fewer_than_none(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text("[storage]\nkeep_logs_days = 7\n", encoding="utf-8")
    assert ServerConfig.from_data_folder(tmp_path).keep_logs_days == 7
    (tmp_path / CONFIG_FILE_NAME).write_text("[storage]\nkeep_logs_days = -1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="keep_logs_days"):
        ServerConfig.from_data_folder(tmp_path)


def test_the_database_writes_ahead_so_that_pages_read_while_a_result_is_written(tmp_path: Path) -> None:
    engine = create_database_engine(server_config_in(tmp_path).database_url)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()


def server_database_in(data_folder: Path) -> Path:
    """The database of a server on `data_folder`, made with its tables and one user."""
    engine = create_database_engine(server_config_in(data_folder).database_url)
    upgrade_database(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (login, created_at) VALUES ('alice', '2026-10-31 12:00:00')"))
    engine.dispose()
    return data_folder / "scala.db"


def test_a_backup_copies_the_database_while_it_is_open(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database = server_database_in(tmp_path / "data")
    with closing(sqlite3.connect(database)) as open_meanwhile:
        open_meanwhile.execute("INSERT INTO users (login, created_at) VALUES ('bob', '2026-10-31 12:01:00')")
        open_meanwhile.commit()
        assert main(["server", "backup", "--data", str(tmp_path / "data"), str(tmp_path / "copies" / "first.db")]) == 0
    assert "Copied the database" in capsys.readouterr().out
    with closing(sqlite3.connect(tmp_path / "copies" / "first.db")) as copy:
        assert [login for (login,) in copy.execute("SELECT login FROM users ORDER BY login")] == ["alice", "bob"]
    assert main(["server", "--data", str(tmp_path / "data"), "backup", str(tmp_path / "second.db")]) == 0
    assert (tmp_path / "second.db").is_file()


def test_a_backup_refuses_a_copy_that_exists_and_a_folder_with_no_database(tmp_path: Path) -> None:
    server_database_in(tmp_path / "data")
    (tmp_path / "taken.db").write_bytes(b"something")
    assert main(["server", "backup", "--data", str(tmp_path / "data"), str(tmp_path / "taken.db")]) == 1
    assert (tmp_path / "taken.db").read_bytes() == b"something"
    (tmp_path / "empty").mkdir()
    assert main(["server", "backup", "--data", str(tmp_path / "empty"), str(tmp_path / "copy.db")]) == 1
    assert not (tmp_path / "copy.db").exists()
    with pytest.raises(SystemExit):
        main(["server", "backup", str(tmp_path / "copy.db")])


def test_the_dev_sign_in_needs_a_local_host(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text('host = "0.0.0.0"\ndev_sign_in = true\n', encoding="utf-8")
    with pytest.raises(ValueError, match="local host"):
        ServerConfig.from_data_folder(tmp_path)


def test_the_migrations_make_the_tables_the_models_describe(tmp_path: Path) -> None:
    engine = create_database_engine(server_config_in(tmp_path).database_url)
    upgrade_database(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


def test_the_dev_sign_in_needs_a_local_public_url_behind_a_proxy(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'host = "127.0.0.1"\npublic_url = "https://scala.example.org"\ndev_sign_in = true\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="local public_url"):
        ServerConfig.from_data_folder(tmp_path)
