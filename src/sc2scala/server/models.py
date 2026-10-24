"""The server's tables, as SQLAlchemy models. A change here needs a migration: `tools/new_migration.py`."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, MetaData, String, Table, TypeDecorator, UniqueConstraint
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
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
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


class BotRecord(Base):
    """A bot on the server: its name, unique here in any case, its race, and its author."""

    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    race: Mapped[str] = mapped_column(String(1))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    owner: Mapped[User] = relationship()
    versions: Mapped[list["BotVersion"]] = relationship(
        back_populates="bot", order_by="BotVersion.number", cascade="all, delete-orphan"
    )

    @property
    def latest_version(self) -> "BotVersion | None":
        return self.versions[-1] if self.versions else None


class BotVersion(Base):
    """An upload of a bot, numbered from 1, its zip kept by its SHA-256, and never changed."""

    __tablename__ = "bot_versions"
    __table_args__ = (UniqueConstraint("bot_id", "number"), UniqueConstraint("bot_id", "sha256"))

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    number: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    bot_type: Mapped[str] = mapped_column(String(20))
    uploaded_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    bot: Mapped[BotRecord] = relationship(back_populates="versions")


class MapFile(Base):
    """A map, by its name and its file, which any number of ladders may hold; the file is kept once by its SHA-256,
    whatever names it was uploaded under."""

    __tablename__ = "maps"
    __table_args__ = (UniqueConstraint("name", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int]
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


ladder_maps = Table(
    "ladder_maps",
    Base.metadata,
    Column("ladder_id", ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True),
    Column("map_id", ForeignKey("maps.id", ondelete="CASCADE"), primary_key=True),
)


class Ladder(Base):
    """A ladder: its name, unique here in any case, its owner, its password's hash if it has one, its maps, and the
    limits of its matches, as `GameLimits` and `ResourceLimits` take them."""

    __tablename__ = "ladders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    game_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resource_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    owner: Mapped[User] = relationship()
    maps: Mapped[list[MapFile]] = relationship(secondary=ladder_maps, order_by=MapFile.name)
    members: Mapped[list["LadderMember"]] = relationship(back_populates="ladder", cascade="all, delete-orphan")
    entries: Mapped[list["LadderEntry"]] = relationship(back_populates="ladder", cascade="all, delete-orphan")

    @property
    def is_protected(self) -> bool:
        return self.password_hash is not None


class LadderMember(Base):
    """Someone who gave a protected ladder's password, or made the ladder."""

    __tablename__ = "ladder_members"

    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    ladder: Mapped[Ladder] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class LadderEntry(Base):
    """A bot entered in a ladder, which plays its latest version there unless paused, by its author or after
    `failed_starts_in_a_row` reached its limit."""

    __tablename__ = "ladder_entries"
    __table_args__ = (UniqueConstraint("ladder_id", "bot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    entered_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    paused: Mapped[bool] = mapped_column(default=False)
    paused_because: Mapped[str | None] = mapped_column(String(200))
    failed_starts_in_a_row: Mapped[int] = mapped_column(default=0)

    ladder: Mapped[Ladder] = relationship(back_populates="entries")
    bot: Mapped[BotRecord] = relationship()
