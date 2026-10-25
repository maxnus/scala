"""Login requests: a command signing in through the browser

Revision ID: 0fe185958faf
Revises: 2934f6e92055
Created: 2026-10-25 08:55:36.973314
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sc2scala.server.models

revision: str = "0fe185958faf"
down_revision: str | None = "2934f6e92055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=9), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("from_address", sa.String(length=64), nullable=True),
        sa.Column("requested_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.Column("expires_at", sc2scala.server.models.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_requests")),
        sa.UniqueConstraint("code", name=op.f("uq_login_requests_code")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_requests_token_hash")),
    )
    with op.batch_alter_table("login_requests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_requests_expires_at"), ["expires_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("login_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_requests_expires_at"))

    op.drop_table("login_requests")
