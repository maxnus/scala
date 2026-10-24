"""The server's tables, as SQLAlchemy models. A change here needs a migration: `tools/new_migration.py`."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, String, TypeDecorator
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UtcDateTime(TypeDecorator[datetime]):
    """A point in time, stored as UTC without a time zone and read back as UTC with one."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"A time to store needs a time zone: {value}")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


def utc_now() -> datetime:
    """The present, in UTC."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """The models' base, which names their constraints by the convention below."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class User(Base):
    """Someone who signed in: with GitHub, then holding its account's id, or with the dev sign-in, then holding none."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    github_id: Mapped[int | None] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    api_tokens: Mapped[list["ApiToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ApiToken(Base):
    """A token with which the CLI and the runners act for a user; the server keeps its hash alone."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    user: Mapped[User] = relationship(back_populates="api_tokens")
