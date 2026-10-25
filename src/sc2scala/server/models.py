"""The server's tables, as SQLAlchemy models. A change here needs a migration: `tools/new_migration.py`."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, MetaData, String, Table, TypeDecorator, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MACHINE_NAME_LENGTH = 40


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
    barred_runners: Mapped[list["BarredRunner"]] = relationship(back_populates="ladder", cascade="all, delete-orphan")

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

    @property
    def paused_note(self) -> str:
        """Why the entry is paused, as a page shows it: "paused by alice", or "paused: failed to start 5 times in a
        row"; empty if it is not."""
        if not self.paused:
            return ""
        because = self.paused_because or ""
        return because if because.startswith("paused") else f"paused: {because}".rstrip(": ")


runner_machine_ladders = Table(
    "runner_machine_ladders",
    Base.metadata,
    Column("machine_id", ForeignKey("runner_machines.id", ondelete="CASCADE"), primary_key=True),
    Column("ladder_id", ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True),
)


class RunnerMachine(Base):
    """A machine whose runners play matches for its owner: every public ladder if `serves_public`, and the ladders it
    lists, a protected one only while its owner is a member. It acts by a token of its own, kept as its hash."""

    __tablename__ = "runner_machines"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(MACHINE_NAME_LENGTH))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    serves_public: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    owner: Mapped[User] = relationship()
    ladders: Mapped[list[Ladder]] = relationship(secondary=runner_machine_ladders, order_by=Ladder.name)

    @property
    def runner_name(self) -> str:
        """The machine as `owner/name`."""
        return f"{self.owner.login}/{self.name}"


class MatchRecord(Base):
    """A match drawn for a runner: leased to it until `deadline`, then finished with its result, or void.

    Each seat holds the bot's version and the race it played; the files a runner uploaded are kept by their SHA-256.
    """

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), index=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("maps.id"))
    player_1_version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id", ondelete="CASCADE"), index=True)
    player_2_version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id", ondelete="CASCADE"), index=True)
    player_1_race: Mapped[str] = mapped_column(String(1))
    player_2_race: Mapped[str] = mapped_column(String(1))
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("runner_machines.id", ondelete="SET NULL"), index=True)
    runner_owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    runner_machine_name: Mapped[str | None] = mapped_column(String(MACHINE_NAME_LENGTH))
    """The owner and the name of the machine that played it, which stay once the machine is removed."""
    status: Mapped[str] = mapped_column(String(10), index=True)
    drawn_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    deadline: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    void_because: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[str | None] = mapped_column(String(30))
    game_steps: Mapped[int | None]
    player_1_average_step_time: Mapped[float | None]
    player_2_average_step_time: Mapped[float | None]
    replay_sha256: Mapped[str | None] = mapped_column(String(64))
    player_1_logs_sha256: Mapped[str | None] = mapped_column(String(64))
    player_2_logs_sha256: Mapped[str | None] = mapped_column(String(64))
    engine_logs_sha256: Mapped[str | None] = mapped_column(String(64))

    ladder: Mapped[Ladder] = relationship()
    map_file: Mapped[MapFile] = relationship()
    player_1_version: Mapped[BotVersion] = relationship(foreign_keys=[player_1_version_id])
    player_2_version: Mapped[BotVersion] = relationship(foreign_keys=[player_2_version_id])
    machine: Mapped[RunnerMachine | None] = relationship()
    runner_owner: Mapped[User | None] = relationship()

    @property
    def versions(self) -> tuple[BotVersion, BotVersion]:
        return self.player_1_version, self.player_2_version

    @property
    def runner_name(self) -> str | None:
        """The machine that played it, as `owner/name` by its owner's login now, or `None` if it is not known."""
        if self.runner_owner is None:
            return None
        return f"{self.runner_owner.login}/{self.runner_machine_name}"


class BarredRunner(Base):
    """A runner machine, by its owner and its name, that a ladder's owner barred from playing its matches, whatever
    the owner's login becomes; whether the bar voided the results it reported there."""

    __tablename__ = "barred_runners"

    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    machine_name: Mapped[str] = mapped_column(String(MACHINE_NAME_LENGTH), primary_key=True)
    barred_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    results_voided: Mapped[bool]

    ladder: Mapped[Ladder] = relationship(back_populates="barred_runners")
    owner: Mapped[User] = relationship()

    @property
    def runner_name(self) -> str:
        """The barred machine as `owner/name`, by its owner's login now."""
        return f"{self.owner.login}/{self.machine_name}"

    def bars(self, machine: RunnerMachine) -> bool:
        return self.owner_id == machine.owner_id and self.machine_name == machine.name


class Rating(Base):
    """A bot version's rating in a ladder, as last fitted, in Elo points about the ladder's mean, with its standard
    error and the games it counted."""

    __tablename__ = "ratings"

    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id", ondelete="CASCADE"), primary_key=True)
    rating: Mapped[float]
    standard_error: Mapped[float]
    games: Mapped[int]
    wins: Mapped[int]
    losses: Mapped[int]
    ties: Mapped[int]
    crashes: Mapped[int]
    fitted_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    version: Mapped[BotVersion] = relationship()
