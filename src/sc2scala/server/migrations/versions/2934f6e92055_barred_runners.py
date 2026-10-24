"""Barred runners, and each match's runner by its owner and name, which stay once its machine is removed

Revision ID: 2934f6e92055
Revises: b9529115a290
Created: 2026-10-24 19:20:11.200133
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "2934f6e92055"
down_revision: str | None = "b9529115a290"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "barred_runners",
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("machine_name", sa.String(length=40), nullable=False),
        sa.Column("barred_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("results_voided", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_barred_runners_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_barred_runners_owner_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ladder_id", "owner_id", "machine_name", name=op.f("pk_barred_runners")),
    )
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("runner_owner_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("runner_machine_name", sa.String(length=40), nullable=True))
        batch_op.create_index(batch_op.f("ix_matches_runner_owner_id"), ["runner_owner_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_matches_runner_owner_id_users"), "users", ["runner_owner_id"], ["id"], ondelete="SET NULL"
        )
    machine_of_match = "FROM runner_machines WHERE runner_machines.id = matches.machine_id"
    op.execute(
        f"UPDATE matches SET runner_owner_id = (SELECT owner_id {machine_of_match}),"
        f" runner_machine_name = (SELECT name {machine_of_match}) WHERE machine_id IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_matches_runner_owner_id_users"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_matches_runner_owner_id"))
        batch_op.drop_column("runner_machine_name")
        batch_op.drop_column("runner_owner_id")

    op.drop_table("barred_runners")
