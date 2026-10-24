"""users and api tokens

Revision ID: 9b5a07094b74
Revises:
Created: 2026-10-24 13:59:58.088226
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "9b5a07094b74"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("github_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("github_id", name=op.f("uq_users_github_id")),
        sa.UniqueConstraint("login", name=op.f("uq_users_login")),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("last_used_at", sc2scala.server.models.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_api_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_api_tokens_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_tokens_user_id"))

    op.drop_table("api_tokens")
    op.drop_table("users")
