"""ratings

Revision ID: b9529115a290
Revises: 45ef7f0ece48
Created: 2026-10-24 14:48:00.736511
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "b9529115a290"
down_revision: str | None = "45ef7f0ece48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ratings",
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Double(), nullable=False),
        sa.Column("standard_error", sa.Double(), nullable=False),
        sa.Column("games", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("ties", sa.Integer(), nullable=False),
        sa.Column("crashes", sa.Integer(), nullable=False),
        sa.Column("fitted_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"], ["ladders.id"], name=op.f("fk_ratings_ladder_id_ladders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["bot_versions.id"], name=op.f("fk_ratings_version_id_bot_versions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ladder_id", "version_id", name=op.f("pk_ratings")),
    )


def downgrade() -> None:
    op.drop_table("ratings")
