import stat
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

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
