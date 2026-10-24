"""bots, maps and ladders

Revision ID: 6096e4d06368
Revises: 9b5a07094b74
Created: 2026-10-24 14:24:41.836759
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "6096e4d06368"
down_revision: str | None = "9b5a07094b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("race", sa.String(length=1), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_bots_owner_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bots")),
        sa.UniqueConstraint("name", name=op.f("uq_bots_name")),
    )
    with op.batch_alter_table("bots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_bots_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "ladders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=True),
        sa.Column("game_limits", sa.JSON(), nullable=False),
        sa.Column("resource_limits", sa.JSON(), nullable=False),
        sa.Column("created_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_ladders_owner_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladders")),
        sa.UniqueConstraint("name", name=op.f("uq_ladders_name")),
    )
    with op.batch_alter_table("ladders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ladders_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "maps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"], ["users.id"], name=op.f("fk_maps_uploaded_by_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maps")),
        sa.UniqueConstraint("name", "sha256", name=op.f("uq_maps_name_sha256")),
    )
    with op.batch_alter_table("maps", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_maps_sha256"), ["sha256"], unique=False)

    op.create_table(
        "bot_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("bot_type", sa.String(length=20), nullable=False),
        sa.Column("uploaded_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], name=op.f("fk_bot_versions_bot_id_bots"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_versions")),
        sa.UniqueConstraint("bot_id", "number", name=op.f("uq_bot_versions_bot_id_number")),
        sa.UniqueConstraint("bot_id", "sha256", name=op.f("uq_bot_versions_bot_id_sha256")),
    )
    with op.batch_alter_table("bot_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_bot_versions_bot_id"), ["bot_id"], unique=False)

    op.create_table(
        "ladder_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("entered_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("paused_because", sa.String(length=200), nullable=True),
        sa.Column("failed_starts_in_a_row", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bot_id"], ["bots.id"], name=op.f("fk_ladder_entries_bot_id_bots"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_ladder_entries_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_entries")),
        sa.UniqueConstraint("ladder_id", "bot_id", name=op.f("uq_ladder_entries_ladder_id_bot_id")),
    )
    with op.batch_alter_table("ladder_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ladder_entries_bot_id"), ["bot_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ladder_entries_ladder_id"), ["ladder_id"], unique=False)

    op.create_table(
        "ladder_maps",
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("map_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_ladder_maps_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["map_id"], ["maps.id"], name=op.f("fk_ladder_maps_map_id_maps"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ladder_id", "map_id", name=op.f("pk_ladder_maps")),
    )
    op.create_table(
        "ladder_members",
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("joined_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_ladder_members_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ladder_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ladder_id", "user_id", name=op.f("pk_ladder_members")),
    )


def downgrade() -> None:
    op.drop_table("ladder_members")
    op.drop_table("ladder_maps")
    with op.batch_alter_table("ladder_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_entries_ladder_id"))
        batch_op.drop_index(batch_op.f("ix_ladder_entries_bot_id"))

    op.drop_table("ladder_entries")
    with op.batch_alter_table("bot_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_bot_versions_bot_id"))

    op.drop_table("bot_versions")
    with op.batch_alter_table("maps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_maps_sha256"))

    op.drop_table("maps")
    with op.batch_alter_table("ladders", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladders_owner_id"))

    op.drop_table("ladders")
    with op.batch_alter_table("bots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_bots_owner_id"))

    op.drop_table("bots")
