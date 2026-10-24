"""Writes a migration for what the server's models changed, by comparing them with the latest migration's tables.

    uv run python tools/new_migration.py "add bots"

The migration lands in `src/sc2scala/server/migrations/versions`, without Alembic's comments; format it with
`uv run ruff format`, and read it before committing it, since Alembic cannot see every change, a renamed column for one.
"""

import argparse
import tempfile
from pathlib import Path

from alembic import command

from sc2scala.server.database import alembic_config, create_database_engine, upgrade_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("message", help="what the migration does, in a few words")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        engine = create_database_engine(f"sqlite:///{Path(scratch) / 'latest.db'}")
        upgrade_database(engine)
        script = command.revision(alembic_config(engine), message=args.message, autogenerate=True)
        engine.dispose()
    for migration in script if isinstance(script, list) else [script]:
        if migration is not None:
            _remove_alembics_comments(Path(migration.path))


def _remove_alembics_comments(migration_file: Path) -> None:
    lines = migration_file.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if not line.strip().startswith("# ###")]
    migration_file.write_text("".join(kept), encoding="utf-8")


if __name__ == "__main__":
    main()
