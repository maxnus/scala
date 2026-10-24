"""A ladder's ratings: which results count and how, refitting them after a result, its standings and pair records."""

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from sc2scala.result_code import ResultCode
from sc2scala.server.bradley_terry import fit_ratings
from sc2scala.server.match_draw import FINISHED
from sc2scala.server.models import BotVersion, Ladder, LadderEntry, MatchRecord, Rating, utc_now

PRIOR_SD = 100.0
"""The ratings' prior, in Elo points: the width that best predicted AI Arena's 2026 season in the matchmaking
prototype's data, by `tools/choose_rating_prior.py`."""

DISPLAYED_MEAN = 1500
"""What the ladder's mean rating is shown as."""

INTERVAL_WIDTH = 1.96
"""Standard errors on each side of a rating, for an interval of 95%."""

_SCORE_OF_PLAYER_1 = {
    ResultCode.PLAYER_1_WIN: 1.0,
    ResultCode.PLAYER_2_WIN: 0.0,
    ResultCode.TIE: 0.5,
    ResultCode.PLAYER_1_CRASH: 0.0,
    ResultCode.PLAYER_1_TIME_OUT: 0.0,
    ResultCode.PLAYER_2_CRASH: 1.0,
    ResultCode.PLAYER_2_TIME_OUT: 1.0,
}
_CRASHES_OF_PLAYER = {
    1: frozenset({ResultCode.PLAYER_1_CRASH, ResultCode.PLAYER_1_TIME_OUT}),
    2: frozenset({ResultCode.PLAYER_2_CRASH, ResultCode.PLAYER_2_TIME_OUT}),
}


def score_of_player_1(code: ResultCode) -> float | None:
    """What player 1 scores by `code`: 1 for a win or the opponent's crash or time-out, ½ for a tie, 0 for a loss, its
    own crash or time-out; `None` for a result that does not count, an error or a failed start."""
    return _SCORE_OF_PLAYER_1.get(code)


@dataclass
class Record:
    """A version's games, against everyone or one opponent."""

    wins: int = 0
    losses: int = 0
    ties: int = 0
    crashes: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties


@dataclass(frozen=True)
class Standing:
    """An entered bot's place in its ladder, by its latest version's rating; unrated before its first counted game."""

    entry: LadderEntry
    version: BotVersion
    rating: Rating | None

    @property
    def displayed_rating(self) -> int | None:
        return None if self.rating is None else round(DISPLAYED_MEAN + self.rating.rating)

    @property
    def interval(self) -> int | None:
        return None if self.rating is None else round(INTERVAL_WIDTH * self.rating.standard_error)


@dataclass
class PairRecords:
    """The records between the entered bots' latest versions, by their bots' names: `records[(bot, opponent)]` is the
    bot's record against the opponent."""

    bots: list[str]
    records: dict[tuple[str, str], Record] = field(default_factory=dict)


def refit_ratings(session: Session, ladder: Ladder) -> None:
    """Fits the ratings of every version that played a counted game in `ladder`, and keeps them in place of the last."""
    counted = [(match, score) for match in _finished_matches(session, ladder) if (score := _score(match)) is not None]
    version_ids = sorted({version_id for match, _ in counted for version_id in _version_ids(match)})
    index_of = {version_id: index for index, version_id in enumerate(version_ids)}
    records = {version_id: Record() for version_id in version_ids}
    for match, score in counted:
        _count(records, match, score)
    session.execute(delete(Rating).where(Rating.ladder_id == ladder.id))
    if not version_ids:
        return
    fitted = fit_ratings(
        len(version_ids),
        [index_of[match.player_1_version_id] for match, _ in counted],
        [index_of[match.player_2_version_id] for match, _ in counted],
        [score for _, score in counted],
        prior_sd=PRIOR_SD,
    )
    now = utc_now()
    for version_id, index in index_of.items():
        record = records[version_id]
        session.add(
            Rating(
                ladder_id=ladder.id,
                version_id=version_id,
                rating=float(fitted.ratings[index]),
                standard_error=float(fitted.standard_errors[index]),
                games=record.games,
                wins=record.wins,
                losses=record.losses,
                ties=record.ties,
                crashes=record.crashes,
                fitted_at=now,
            )
        )


def standings(session: Session, ladder: Ladder) -> list[Standing]:
    """The ladder's entered bots, the rated by rating, highest first, then the unrated by name."""
    ratings = {
        rating.version_id: rating for rating in session.scalars(select(Rating).where(Rating.ladder_id == ladder.id))
    }
    placed = []
    for entry in ladder.entries:
        version = entry.bot.latest_version
        if version is not None:
            placed.append(Standing(entry=entry, version=version, rating=ratings.get(version.id)))
    return sorted(
        placed,
        key=lambda standing: (
            standing.rating is None,
            -(standing.rating.rating if standing.rating else 0),
            standing.entry.bot.name.lower(),
        ),
    )


def pair_records(session: Session, ladder: Ladder) -> PairRecords:
    """The records between the entered bots' latest versions, in counted games."""
    latest = {
        entry.bot.latest_version.id: entry.bot.name for entry in ladder.entries if entry.bot.latest_version is not None
    }
    pairs = PairRecords(bots=sorted(latest.values(), key=str.lower))
    for match in _finished_matches(session, ladder):
        score = _score(match)
        bot_1 = latest.get(match.player_1_version_id)
        bot_2 = latest.get(match.player_2_version_id)
        if score is None or bot_1 is None or bot_2 is None:
            continue
        for bot, opponent, own_score, seat in ((bot_1, bot_2, score, 1), (bot_2, bot_1, 1 - score, 2)):
            record = pairs.records.setdefault((bot, opponent), Record())
            _add_game(record, own_score, crashed=ResultCode(match.result) in _CRASHES_OF_PLAYER[seat])
    return pairs


def _finished_matches(session: Session, ladder: Ladder) -> list[MatchRecord]:
    query = select(MatchRecord).where(MatchRecord.ladder_id == ladder.id, MatchRecord.status == FINISHED)
    return list(session.scalars(query.options(selectinload(MatchRecord.player_1_version))))


def _score(match: MatchRecord) -> float | None:
    return score_of_player_1(ResultCode(match.result)) if match.result else None


def _version_ids(match: MatchRecord) -> tuple[int, int]:
    return match.player_1_version_id, match.player_2_version_id


def _count(records: dict[int, Record], match: MatchRecord, score: float) -> None:
    code = ResultCode(match.result)
    _add_game(records[match.player_1_version_id], score, crashed=code in _CRASHES_OF_PLAYER[1])
    _add_game(records[match.player_2_version_id], 1 - score, crashed=code in _CRASHES_OF_PLAYER[2])


def _add_game(record: Record, score: float, *, crashed: bool) -> None:
    if score == 1:
        record.wins += 1
    elif score == 0:
        record.losses += 1
    else:
        record.ties += 1
    if crashed:
        record.crashes += 1
