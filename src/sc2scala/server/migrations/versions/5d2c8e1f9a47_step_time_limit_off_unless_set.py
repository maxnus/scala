"""A ladder's step-time limit is off unless its owner sets one: the old default of 40 ms, which no engine applied,
is taken out of the ladders that hold it

Revision ID: 5d2c8e1f9a47
Revises: 8a87638e067c
Created: 2026-11-01 15:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d2c8e1f9a47"
down_revision: str | None = "8a87638e067c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_DEFAULT_MS = 40
_ladders = sa.table("ladders", sa.column("id", sa.Integer), sa.column("game_limits", sa.JSON))


def upgrade() -> None:
    _replace_step_time_limit(_OLD_DEFAULT_MS, None)


def downgrade() -> None:
    _replace_step_time_limit(None, _OLD_DEFAULT_MS)


def _replace_step_time_limit(old: int | None, new: int | None) -> None:
    """Sets the step-time limit of each ladder whose limits hold `old` for it to `new`."""
    connection = op.get_bind()
    for ladder_id, game_limits in connection.execute(sa.select(_ladders.c.id, _ladders.c.game_limits)).all():
        if game_limits and "max_step_time_ms" in game_limits and game_limits["max_step_time_ms"] == old:
            changed = {**game_limits, "max_step_time_ms": new}
            connection.execute(sa.update(_ladders).where(_ladders.c.id == ladder_id).values(game_limits=changed))
