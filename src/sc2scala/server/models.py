"""The server's tables, as SQLAlchemy models. A change here needs a migration: `tools/new_migration.py`."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sc2scala.computer import Build, Computer, Difficulty
from sc2scala.race import Race

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


class LoginRequest(Base):
    """A command's request to act for whoever confirms its `code` on the pages, before `expires_at`.

    The command made its token itself, and sent its hash alone: confirming makes that hash an `ApiToken` of the user's,
    named `name`, and removes the request.
    """

    __tablename__ = "login_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(9), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    from_address: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)


class BotRecord(Base):
    """A bot on the server: its name, unique here in any case, its race, and its author.

    A bot that played is retired rather than deleted, at `retired_at`, so that its matches keep their players: it is in
    no ladder, takes no version, and keeps its name.
    """

    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    race: Mapped[str] = mapped_column(String(1))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    retired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

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
    limits of its matches, as `GameLimits` and `ResourceLimits` take them.

    A ladder that played is retired rather than deleted, at `retired_at`: it draws no match and takes no entry, and
    keeps its standings, matches and name.
    """

    __tablename__ = "ladders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    game_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resource_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    retired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    owner: Mapped[User] = relationship()
    maps: Mapped[list[MapFile]] = relationship(secondary=ladder_maps, order_by=MapFile.name)
    members: Mapped[list["LadderMember"]] = relationship(back_populates="ladder", cascade="all, delete-orphan")
    entries: Mapped[list["LadderEntry"]] = relationship(back_populates="ladder", cascade="all, delete-orphan")
    computers: Mapped[list["LadderComputer"]] = relationship(
        back_populates="ladder", order_by="LadderComputer.id", cascade="all, delete-orphan"
    )
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


class LadderComputer(Base):
    """The game's built-in computer as a player of a ladder, of a race, difficulty and build as `Computer` takes them,
    each such computer once in its ladder. It plays there unless paused.

    One that played is retired rather than deleted when its ladder's owner removes it, at `retired_at`, so that its
    matches keep their player; added again, it is the same player, its record and rating kept.
    """

    __tablename__ = "ladder_computers"
    __table_args__ = (UniqueConstraint("ladder_id", "race", "difficulty", "build"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), index=True)
    race: Mapped[str] = mapped_column(String(1))
    difficulty: Mapped[str] = mapped_column(String(20))
    build: Mapped[str] = mapped_column(String(20))
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    paused: Mapped[bool] = mapped_column(default=False)
    retired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    ladder: Mapped[Ladder] = relationship(back_populates="computers")

    @property
    def computer(self) -> Computer:
        return Computer(race=Race(self.race), difficulty=Difficulty(self.difficulty), build=Build(self.build))

    @property
    def name(self) -> str:
        """The computer's name, as `Computer-VeryHard-Zerg-Rush`."""
        return self.computer.name


runner_machine_ladders = Table(
    "runner_machine_ladders",
    Base.metadata,
    Column("machine_id", ForeignKey("runner_machines.id", ondelete="CASCADE"), primary_key=True),
    Column("ladder_id", ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True),
)


class RunnerMachine(Base):
    """A machine whose runners play matches for its owner: every public ladder if `serves_public`, and the ladders it
    lists, a protected one only while its owner is a member. It acts by a token of its own, kept as its hash.

    Its owner sets how many matches it plays at once, `matches_at_once`, and pauses it; its runner says, each time it
    asks for a match, the most it allows, its Scala and its engine, and when its next window opens if none is open. A
    runner of a release before these controls says none of them, and plays as its own file says.
    """

    __tablename__ = "runner_machines"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(MACHINE_NAME_LENGTH))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    serves_public: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    matches_at_once: Mapped[int] = mapped_column(default=1)
    paused: Mapped[bool] = mapped_column(default=False)
    max_matches_at_once: Mapped[int | None]
    scala_version: Mapped[str | None] = mapped_column(String(40))
    engine: Mapped[str | None] = mapped_column(String(20))
    next_window_opens: Mapped[str | None] = mapped_column(String(16))
    """When its next window opens, in its own time, as "2026-11-02T23:00", if none was open when its runner last
    asked."""
    last_answered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    """When the server last answered its runner's ask, with every match leased to it by then."""

    owner: Mapped[User] = relationship()
    ladders: Mapped[list[Ladder]] = relationship(secondary=runner_machine_ladders, order_by=Ladder.name)

    @property
    def runner_name(self) -> str:
        """The machine as `owner/name`."""
        return f"{self.owner.login}/{self.name}"

    @property
    def matches_it_may_play_at_once(self) -> int:
        """Its owner's number, but no more than its runner allows, once it said."""
        return min(self.matches_at_once, self.max_matches_at_once or self.matches_at_once)

    @property
    def follows_its_controls(self) -> bool:
        """Whether its runner, as of its last ask, is of a release that follows its owner's number and pause, which
        one before step 9 does not."""
        return self.scala_version is not None


runner_setup_code_ladders = Table(
    "runner_setup_code_ladders",
    Base.metadata,
    Column("setup_code_id", ForeignKey("runner_setup_codes.id", ondelete="CASCADE"), primary_key=True),
    Column("ladder_id", ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True),
)


class RunnerSetupCode(Base):
    """A code that registers one runner machine of its user's, serving what it says, until `expires_at`.

    Kept as its hash, as a token is, and removed once used: exchanging it needs no other token.
    """

    __tablename__ = "runner_setup_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    serves_public: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)

    user: Mapped[User] = relationship()
    ladders: Mapped[list[Ladder]] = relationship(secondary=runner_setup_code_ladders, order_by=Ladder.name)


class MatchRecord(Base):
    """A match drawn for a runner: leased to it until `deadline`, then finished with its result, or void.

    Each seat holds the bot's version and the race it played, but seat 2 holds the computer instead in a match against
    it, and never both; the files a runner uploaded are kept by their SHA-256.
    """

    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("(player_2_version_id IS NULL) != (player_2_computer_id IS NULL)", name="one_player_in_seat_2"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), index=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("maps.id"))
    player_1_version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id", ondelete="CASCADE"), index=True)
    player_2_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_versions.id", ondelete="CASCADE"), index=True
    )
    player_2_computer_id: Mapped[int | None] = mapped_column(
        ForeignKey("ladder_computers.id", ondelete="CASCADE"), index=True
    )
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
    player_2_version: Mapped[BotVersion | None] = relationship(foreign_keys=[player_2_version_id])
    player_2_computer: Mapped[LadderComputer | None] = relationship()
    machine: Mapped[RunnerMachine | None] = relationship()
    runner_owner: Mapped[User | None] = relationship()

    @property
    def bot_versions(self) -> tuple[BotVersion, ...]:
        """The versions of the match's bots, in their seats: player 1's, and player 2's unless it is the computer."""
        if self.player_2_version is None:
            return (self.player_1_version,)
        return self.player_1_version, self.player_2_version

    @property
    def player_names(self) -> tuple[str, str]:
        """Each seat's bot's name, or the computer's."""
        return self.player_1_version.bot.name, self.player_2_name

    @property
    def player_2_name(self) -> str:
        if self.player_2_computer is not None:
            return self.player_2_computer.name
        assert self.player_2_version is not None
        return self.player_2_version.bot.name

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


class RatingFigures:
    """A player's rating in a ladder, as last fitted, in Elo points about the ladder's mean, with its standard error and
    the games it counted."""

    rating: Mapped[float]
    standard_error: Mapped[float]
    games: Mapped[int]
    wins: Mapped[int]
    losses: Mapped[int]
    ties: Mapped[int]
    crashes: Mapped[int]
    fitted_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class Rating(RatingFigures, Base):
    """A bot version's rating in a ladder."""

    __tablename__ = "ratings"

    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id", ondelete="CASCADE"), primary_key=True)

    version: Mapped[BotVersion] = relationship()


class ComputerRating(RatingFigures, Base):
    """A computer's rating in its ladder."""

    __tablename__ = "computer_ratings"

    ladder_id: Mapped[int] = mapped_column(ForeignKey("ladders.id", ondelete="CASCADE"), primary_key=True)
    computer_id: Mapped[int] = mapped_column(ForeignKey("ladder_computers.id", ondelete="CASCADE"), primary_key=True)

    computer: Mapped[LadderComputer] = relationship()
