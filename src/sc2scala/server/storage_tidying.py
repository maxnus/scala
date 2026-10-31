"""Keeping the server's files in bounds: a match's logs dropped once they are old, and the files no row holds removed.

A file is kept once by its hash, however many rows hold it, so a row that lets go of one leaves it, and the sweep
removes it once no row holds it. An upload is kept before its row is written, so the sweep spares a file kept within
`UNHELD_FILE_GRACE`; it removes what a failed upload left once that has passed.
"""

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import Engine, or_, select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

from sc2scala.server.bots_api import BOT_ARCHIVE_SUFFIX, BOT_ARCHIVES
from sc2scala.server.file_store import PARTIAL_UPLOAD_PREFIX, FileStore
from sc2scala.server.ladders_api import MAP_FILES
from sc2scala.server.models import BotVersion, MapFile, MatchRecord, utc_now
from sc2scala.server.runner_api import LOGS, LOGS_SUFFIX, REPLAY_SUFFIX, REPLAYS
from sc2scala.server.upload_checks import MAP_SUFFIX

TIDY_EVERY = timedelta(hours=1)
UNHELD_FILE_GRACE = timedelta(days=1)
_STORED_NAME = re.compile(r"[0-9a-f]{64}")
_KINDS_AND_SUFFIXES = (
    (BOT_ARCHIVES, BOT_ARCHIVE_SUFFIX),
    (MAP_FILES, MAP_SUFFIX),
    (REPLAYS, REPLAY_SUFFIX),
    (LOGS, LOGS_SUFFIX),
)


@dataclass(frozen=True)
class StorageTidied:
    """What one tidying did."""

    matches_logs_dropped: int
    """How many matches' logs were dropped for their age."""
    files_removed: int
    bytes_freed: int


def tidy_storage(engine: Engine, file_store: FileStore, *, keep_logs_days: int, now: datetime) -> StorageTidied:
    """Drops the logs of the matches that ended more than `keep_logs_days` before `now`, none if 0, then removes the
    files no row holds, but for those kept within `UNHELD_FILE_GRACE`."""
    with Session(engine) as session:
        dropped = drop_old_logs(session, keep_logs_days=keep_logs_days, now=now)
        session.commit()
        held = held_files(session)
    removed, freed = remove_unheld_files(file_store, held, kept_before=now - UNHELD_FILE_GRACE)
    return StorageTidied(matches_logs_dropped=dropped, files_removed=removed, bytes_freed=freed)


def drop_old_logs(session: Session, *, keep_logs_days: int, now: datetime) -> int:
    """Forgets the logs of the matches that ended more than `keep_logs_days` before `now`, none if 0, and returns how
    many matches had any; their files go at the next sweep."""
    if keep_logs_days == 0:
        return 0
    has_logs = or_(
        MatchRecord.player_1_logs_sha256.is_not(None),
        MatchRecord.player_2_logs_sha256.is_not(None),
        MatchRecord.engine_logs_sha256.is_not(None),
    )
    old_enough = MatchRecord.finished_at < now - timedelta(days=keep_logs_days)
    match_ids = list(session.scalars(select(MatchRecord.id).where(old_enough, has_logs)))
    if match_ids:
        session.execute(
            update(MatchRecord)
            .where(MatchRecord.id.in_(match_ids))
            .values(player_1_logs_sha256=None, player_2_logs_sha256=None, engine_logs_sha256=None)
        )
    return len(match_ids)


def held_files(session: Session) -> dict[str, set[str]]:
    """The hashes of the files some row holds, by their kind."""
    return {
        BOT_ARCHIVES: _hashes_in(session, BotVersion.sha256),
        MAP_FILES: _hashes_in(session, MapFile.sha256),
        REPLAYS: _hashes_in(session, MatchRecord.replay_sha256),
        LOGS: _hashes_in(
            session, MatchRecord.player_1_logs_sha256, MatchRecord.player_2_logs_sha256, MatchRecord.engine_logs_sha256
        ),
    }


def _hashes_in(session: Session, *columns: InstrumentedAttribute[str] | InstrumentedAttribute[str | None]) -> set[str]:
    return {sha256 for column in columns for sha256 in session.scalars(select(column).distinct()) if sha256 is not None}


def remove_unheld_files(file_store: FileStore, held: dict[str, set[str]], *, kept_before: datetime) -> tuple[int, int]:
    """Removes each stored file whose hash `held` lacks, and each partial upload, last changed before `kept_before`;
    returns how many it removed and how many bytes they held. Files the store did not name are left alone."""
    removed = 0
    freed = 0
    for kind, suffix in _KINDS_AND_SUFFIXES:
        folder = file_store.root / kind
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            name = path.name
            stored = name.endswith(suffix) and _STORED_NAME.fullmatch(name.removesuffix(suffix)) is not None
            if not (name.startswith(PARTIAL_UPLOAD_PREFIX) or (stored and name.removesuffix(suffix) not in held[kind])):
                continue
            try:
                status = path.stat()
                if datetime.fromtimestamp(status.st_mtime, kept_before.tzinfo) >= kept_before:
                    continue
                path.unlink()
            except FileNotFoundError:
                continue
            removed += 1
            freed += status.st_size
    return removed, freed


class StorageTidier:
    """Tidies a server's storage once started, and every `TIDY_EVERY` after, on a thread of its own, until stopped."""

    def __init__(self, engine: Engine, file_store: FileStore, *, keep_logs_days: int) -> None:
        self._engine = engine
        self._file_store = file_store
        self._keep_logs_days = keep_logs_days
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._tidy_until_stopped, name="storage-tidier", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stops it, after the tidying under way, if any, ends."""
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join()

    def _tidy_until_stopped(self) -> None:
        while True:
            try:
                tidied = tidy_storage(
                    self._engine, self._file_store, keep_logs_days=self._keep_logs_days, now=utc_now()
                )
            except Exception:
                logger.exception("Tidying the storage failed; it is tried again later")
            else:
                if tidied.matches_logs_dropped:
                    logger.info(
                        f"Dropped the logs of {tidied.matches_logs_dropped} matches older than {self._keep_logs_days} "
                        "days"
                    )
                if tidied.files_removed:
                    logger.info(
                        f"Removed {tidied.files_removed} files no row holds, {tidied.bytes_freed / 1024**2:.1f} MiB"
                    )
            if self._stopping.wait(TIDY_EVERY.total_seconds()):
                return
