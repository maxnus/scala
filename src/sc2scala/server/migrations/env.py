from alembic import context
from sqlalchemy import Engine

from sc2scala.server.models import Base

_engine: Engine = context.config.attributes["engine"]

with _engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
