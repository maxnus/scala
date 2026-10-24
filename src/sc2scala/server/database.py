from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event

MIGRATIONS_FOLDER = Path(__file__).parent / "migrations"


def create_database_engine(database_url: str) -> Engine:
    """The engine of the database at `database_url`, which on SQLite enforces foreign keys."""
    if not database_url.startswith("sqlite"):
        return create_engine(database_url)
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection: Any, connection_record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def alembic_config(engine: Engine) -> Config:
    """Alembic's config for the migrations of this package, run against `engine`."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_FOLDER))
    config.attributes["engine"] = engine
    return config


def upgrade_database(engine: Engine) -> None:
    """Brings the database's tables up to the latest migration."""
    command.upgrade(alembic_config(engine), "head")
