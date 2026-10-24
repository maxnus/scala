"""Barred runners, and the runner of each match by name, which stays once its machine is removed

Revision ID: 1e81f842d730
Revises: b9529115a290
Created: 2026-10-24 19:08:46.133987
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "1e81f842d730"
down_revision: str | None = "b9529115a290"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "barred_runners",
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("runner", sa.String(length=150), nullable=False),
        sa.Column("barred_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("results_voided", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_barred_runners_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ladder_id", "runner", name=op.f("pk_barred_runners")),
    )
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("runner", sa.String(length=150), nullable=True))
        batch_op.create_index(batch_op.f("ix_matches_runner"), ["runner"], unique=False)
    op.execute(
        "UPDATE matches SET runner = ("
        "SELECT users.login || '/' || runner_machines.name FROM runner_machines"
        " JOIN users ON users.id = runner_machines.owner_id WHERE runner_machines.id = matches.machine_id"
        ") WHERE machine_id IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_matches_runner"))
        batch_op.drop_column("runner")

    op.drop_table("barred_runners")
