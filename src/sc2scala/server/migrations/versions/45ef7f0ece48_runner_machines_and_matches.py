"""runner machines and matches

Revision ID: 45ef7f0ece48
Revises: 6096e4d06368
Created: 2026-10-24 14:30:09.108254
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "45ef7f0ece48"
down_revision: str | None = "6096e4d06368"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_machines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("serves_public", sa.Boolean(), nullable=False),
        sa.Column("created_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("last_seen_at", sc2scala.server.models.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_runner_machines_owner_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_machines")),
        sa.UniqueConstraint("owner_id", "name", name=op.f("uq_runner_machines_owner_id_name")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_runner_machines_token_hash")),
    )
    with op.batch_alter_table("runner_machines", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_runner_machines_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "runner_machine_ladders",
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_runner_machine_ladders_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["runner_machines.id"],
            name=op.f("fk_runner_machine_ladders_machine_id_runner_machines"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("machine_id", "ladder_id", name=op.f("pk_runner_machine_ladders")),
    )
    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("map_id", sa.Integer(), nullable=False),
        sa.Column("player_1_version_id", sa.Integer(), nullable=False),
        sa.Column("player_2_version_id", sa.Integer(), nullable=False),
        sa.Column("player_1_race", sa.String(length=1), nullable=False),
        sa.Column("player_2_race", sa.String(length=1), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("drawn_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("deadline", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("finished_at", sc2scala.server.models.UtcDateTime(), nullable=True),
        sa.Column("void_because", sa.String(length=300), nullable=True),
        sa.Column("result", sa.String(length=30), nullable=True),
        sa.Column("game_steps", sa.Integer(), nullable=True),
        sa.Column("player_1_average_step_time", sa.Double(), nullable=True),
        sa.Column("player_2_average_step_time", sa.Double(), nullable=True),
        sa.Column("replay_sha256", sa.String(length=64), nullable=True),
        sa.Column("player_1_logs_sha256", sa.String(length=64), nullable=True),
        sa.Column("player_2_logs_sha256", sa.String(length=64), nullable=True),
        sa.Column("engine_logs_sha256", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_matches_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["runner_machines.id"],
            name=op.f("fk_matches_machine_id_runner_machines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["map_id"], ["maps.id"], name=op.f("fk_matches_map_id_maps")),
        sa.ForeignKeyConstraint(
            ["player_1_version_id"],
            ["bot_versions.id"],
            name=op.f("fk_matches_player_1_version_id_bot_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_2_version_id"],
            ["bot_versions.id"],
            name=op.f("fk_matches_player_2_version_id_bot_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matches")),
    )
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_matches_ladder_id"), ["ladder_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_machine_id"), ["machine_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_player_1_version_id"), ["player_1_version_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_player_2_version_id"), ["player_2_version_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_matches_status"))
        batch_op.drop_index(batch_op.f("ix_matches_player_2_version_id"))
        batch_op.drop_index(batch_op.f("ix_matches_player_1_version_id"))
        batch_op.drop_index(batch_op.f("ix_matches_machine_id"))
        batch_op.drop_index(batch_op.f("ix_matches_ladder_id"))

    op.drop_table("matches")
    op.drop_table("runner_machine_ladders")
    with op.batch_alter_table("runner_machines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_runner_machines_owner_id"))

    op.drop_table("runner_machines")
