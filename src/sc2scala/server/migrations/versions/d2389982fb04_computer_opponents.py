"""Computer opponents: the game's built-in computer as a ladder's player, in seat 2 of its matches, rated as a bot is

Revision ID: d2389982fb04
Revises: 5d2c8e1f9a47
Created: 2026-11-01 17:47:33.635949
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "d2389982fb04"
down_revision: str | None = "5d2c8e1f9a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ladder_computers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("race", sa.String(length=1), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("build", sa.String(length=20), nullable=False),
        sa.Column("added_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sc2scala.server.models.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_ladder_computers_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_computers")),
        sa.UniqueConstraint(
            "ladder_id", "race", "difficulty", "build", name=op.f("uq_ladder_computers_ladder_id_race_difficulty_build")
        ),
    )
    with op.batch_alter_table("ladder_computers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ladder_computers_ladder_id"), ["ladder_id"], unique=False)

    op.create_table(
        "computer_ratings",
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("computer_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Double(), nullable=False),
        sa.Column("standard_error", sa.Double(), nullable=False),
        sa.Column("games", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("ties", sa.Integer(), nullable=False),
        sa.Column("crashes", sa.Integer(), nullable=False),
        sa.Column("fitted_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["computer_id"],
            ["ladder_computers.id"],
            name=op.f("fk_computer_ratings_computer_id_ladder_computers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_computer_ratings_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ladder_id", "computer_id", name=op.f("pk_computer_ratings")),
    )
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("player_2_computer_id", sa.Integer(), nullable=True))
        batch_op.alter_column("player_2_version_id", existing_type=sa.INTEGER(), nullable=True)
        batch_op.create_index(batch_op.f("ix_matches_player_2_computer_id"), ["player_2_computer_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_matches_player_2_computer_id_ladder_computers"),
            "ladder_computers",
            ["player_2_computer_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_matches_one_player_in_seat_2"),
            "(player_2_version_id IS NULL) != (player_2_computer_id IS NULL)",
        )


def downgrade() -> None:
    # A match against a computer has no version in seat 2, which every match needs again.
    op.execute("DELETE FROM matches WHERE player_2_computer_id IS NOT NULL")
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_matches_one_player_in_seat_2"), type_="check")
        batch_op.drop_constraint(batch_op.f("fk_matches_player_2_computer_id_ladder_computers"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_matches_player_2_computer_id"))
        batch_op.alter_column("player_2_version_id", existing_type=sa.INTEGER(), nullable=False)
        batch_op.drop_column("player_2_computer_id")

    op.drop_table("computer_ratings")
    with op.batch_alter_table("ladder_computers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_computers_ladder_id"))

    op.drop_table("ladder_computers")
