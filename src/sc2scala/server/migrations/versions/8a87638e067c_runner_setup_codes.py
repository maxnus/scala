"""Runner setup codes: a code from the pages that registers one runner machine, used once within an hour

Revision ID: 8a87638e067c
Revises: ccfb3710dfec
Created: 2026-10-31 20:33:22.037858
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "8a87638e067c"
down_revision: str | None = "ccfb3710dfec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_setup_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("serves_public", sa.Boolean(), nullable=False),
        sa.Column("created_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("expires_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_runner_setup_codes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_setup_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_runner_setup_codes_code_hash")),
    )
    with op.batch_alter_table("runner_setup_codes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_runner_setup_codes_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_runner_setup_codes_user_id"), ["user_id"], unique=False)

    op.create_table(
        "runner_setup_code_ladders",
        sa.Column("setup_code_id", sa.Integer(), nullable=False),
        sa.Column("ladder_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ladder_id"],
            ["ladders.id"],
            name=op.f("fk_runner_setup_code_ladders_ladder_id_ladders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["setup_code_id"],
            ["runner_setup_codes.id"],
            name=op.f("fk_runner_setup_code_ladders_setup_code_id_runner_setup_codes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("setup_code_id", "ladder_id", name=op.f("pk_runner_setup_code_ladders")),
    )


def downgrade() -> None:
    op.drop_table("runner_setup_code_ladders")
    with op.batch_alter_table("runner_setup_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_runner_setup_codes_user_id"))
        batch_op.drop_index(batch_op.f("ix_runner_setup_codes_expires_at"))

    op.drop_table("runner_setup_codes")
