"""Retired bots and ladders: those that played, kept for their matches rather than deleted

Revision ID: ccfb3710dfec
Revises: 0fe185958faf
Created: 2026-10-31 09:18:23.851280
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "ccfb3710dfec"
down_revision: str | None = "0fe185958faf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bots", schema=None) as batch_op:
        batch_op.add_column(sa.Column("retired_at", sc2scala.server.models.UtcDateTime(), nullable=True))

    with op.batch_alter_table("ladders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("retired_at", sc2scala.server.models.UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ladders", schema=None) as batch_op:
        batch_op.drop_column("retired_at")

    with op.batch_alter_table("bots", schema=None) as batch_op:
        batch_op.drop_column("retired_at")
