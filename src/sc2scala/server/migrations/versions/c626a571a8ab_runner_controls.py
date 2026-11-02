"""A runner machine's controls: how many matches it plays at once and whether it is paused, set by its owner, and what
its runner says when it asks for a match

Every machine starts at 1 match at once, unpaused: the only one so far played 1 at once.

Revision ID: c626a571a8ab
Revises: d2389982fb04
Created: 2026-11-02 21:37:51.418013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "c626a571a8ab"
down_revision: str | None = "d2389982fb04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runner_machines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("matches_at_once", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("max_matches_at_once", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("scala_version", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("engine", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("next_window_opens", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("last_answered_at", sc2scala.server.models.UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runner_machines", schema=None) as batch_op:
        batch_op.drop_column("last_answered_at")
        batch_op.drop_column("next_window_opens")
        batch_op.drop_column("engine")
        batch_op.drop_column("scala_version")
        batch_op.drop_column("max_matches_at_once")
        batch_op.drop_column("paused")
        batch_op.drop_column("matches_at_once")
