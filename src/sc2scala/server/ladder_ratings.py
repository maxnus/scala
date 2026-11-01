"""A ladder's ratings: which results count and how, refitting them after a result, its standings and pair records.

A ladder's players are its bots' versions and its computers, each rated in one fit of every counted game."""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from sc2scala.result_code import ResultCode
from sc2scala.server.bradley_terry import fit_ratings
from sc2scala.server.match_draw import FINISHED
from sc2scala.server.models import (
    BotVersion,
    ComputerRating,
    Ladder,
    LadderComputer,
    LadderEntry,
    MatchRecord,
    Rating,
    RatingFigures,
    utc_now,
)

PRIOR_SD = 100.0
"""The ratings' prior, in Elo points: the width that best predicted AI Arena's 2026 season in the matchmaking
prototype's data, by `tools/choose_rating_prior.py`."""

DISPLAYED_MEAN = 1500
"""What the mean rating of every player that played in the ladder, version or computer, is shown as."""

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


type PlayerKey = tuple[str, int]
"""A player of a ladder: `("version", id)` for a bot's version, `("computer", id)` for a computer."""
_VERSION = "version"
_COMPUTER = "computer"


@dataclass
class Record:
    """A player's games, against everyone or one opponent."""

    wins: int = 0
    losses: int = 0
    ties: int = 0
    crashes: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties


@dataclass(frozen=True)
class _RatedPlace:
    rating: RatingFigures | None

    @property
    def displayed_rating(self) -> int | None:
        return None if self.rating is None else round(DISPLAYED_MEAN + self.rating.rating)

    @property
    def interval(self) -> int | None:
        return None if self.rating is None else round(INTERVAL_WIDTH * self.rating.standard_error)


@dataclass(frozen=True)
class Standing(_RatedPlace):
    """An entered bot's place in its ladder, by its latest version's rating; unrated before its first counted game."""

    entry: LadderEntry
    version: BotVersion
    is_computer = False

    @property
    def name(self) -> str:
        return self.entry.bot.name


@dataclass(frozen=True)
class ComputerStanding(_RatedPlace):
    """A computer's place in its ladder; unrated before its first counted game."""

    computer: LadderComputer
    is_computer = True

    @property
    def name(self) -> str:
        return self.computer.name


@dataclass
class PairRecords:
    """The records between the ladder's players, its entered bots' latest versions and its computers, by their names:
    `records[(player, opponent)]` is the player's record against the opponent. Two computers never play."""

    players: list[str]
    computers: set[str] = field(default_factory=set)
    """The names among `players` that are computers'."""
    records: dict[tuple[str, str], Record] = field(default_factory=dict)


def refit_after_result(app: Any, ladder_id: int) -> None:
    """Refits the ladder's ratings in a session of its own, holding the app's lock on refits, so that refits of one
    server process run one at a time and each takes in every result committed before it began."""
    with app.state.refit_lock, Session(app.state.engine) as session:
        ladder = session.get(Ladder, ladder_id)
        if ladder is not None:
            refit_ratings(session, ladder)
            session.commit()


def refit_ratings(session: Session, ladder: Ladder) -> None:
    """Fits the ratings of every player that played a counted game in `ladder`, and keeps them in place of the last."""
    counted = [(match, score) for match in _finished_matches(session, ladder) if (score := _score(match)) is not None]
    players = sorted({player for match, _ in counted for player in _players_of(match)})
    index_of = {player: index for index, player in enumerate(players)}
    records = {player: Record() for player in players}
    for match, score in counted:
        _count(records, match, score)
    session.execute(delete(Rating).where(Rating.ladder_id == ladder.id))
    session.execute(delete(ComputerRating).where(ComputerRating.ladder_id == ladder.id))
    if not players:
        return
    fitted = fit_ratings(
        len(players),
        [index_of[_players_of(match)[0]] for match, _ in counted],
        [index_of[_players_of(match)[1]] for match, _ in counted],
        [score for _, score in counted],
        prior_sd=PRIOR_SD,
    )
    now = utc_now()
    for (kind, player_id), index in index_of.items():
        record = records[(kind, player_id)]
        figures = {
            "rating": float(fitted.ratings[index]),
            "standard_error": float(fitted.standard_errors[index]),
            "games": record.games,
            "wins": record.wins,
            "losses": record.losses,
            "ties": record.ties,
            "crashes": record.crashes,
            "fitted_at": now,
        }
        if kind == _COMPUTER:
            session.add(ComputerRating(ladder_id=ladder.id, computer_id=player_id, **figures))
        else:
            session.add(Rating(ladder_id=ladder.id, version_id=player_id, **figures))


def standings(session: Session, ladder: Ladder) -> list[Standing | ComputerStanding]:
    """The ladder's entered bots and its computers but the retired, the rated by rating, highest first, then the
    unrated by name."""
    ratings = {
        rating.version_id: rating for rating in session.scalars(select(Rating).where(Rating.ladder_id == ladder.id))
    }
    computer_ratings = {
        rating.computer_id: rating
        for rating in session.scalars(select(ComputerRating).where(ComputerRating.ladder_id == ladder.id))
    }
    placed: list[Standing | ComputerStanding] = []
    for entry in ladder.entries:
        version = entry.bot.latest_version
        if version is not None:
            placed.append(Standing(entry=entry, version=version, rating=ratings.get(version.id)))
    for computer in playing_computers(ladder):
        placed.append(ComputerStanding(computer=computer, rating=computer_ratings.get(computer.id)))
    return sorted(
        placed,
        key=lambda standing: (
            standing.rating is None,
            -(standing.rating.rating if standing.rating else 0),
            standing.name.lower(),
        ),
    )


def playing_computers(ladder: Ladder) -> list[LadderComputer]:
    """The ladder's computers but the retired, paused or not."""
    return [computer for computer in ladder.computers if computer.retired_at is None]


def pair_records(session: Session, ladder: Ladder) -> PairRecords:
    """The records between the ladder's players, its entered bots' latest versions and its computers but the retired,
    in counted games."""
    names: dict[PlayerKey, str] = {
        (_VERSION, entry.bot.latest_version.id): entry.bot.name
        for entry in ladder.entries
        if entry.bot.latest_version is not None
    }
    computers = {(_COMPUTER, computer.id): computer.name for computer in playing_computers(ladder)}
    names |= computers
    pairs = PairRecords(players=sorted(names.values(), key=str.lower), computers=set(computers.values()))
    for match in _finished_matches(session, ladder):
        score = _score(match)
        player_1, player_2 = (names.get(player) for player in _players_of(match))
        if score is None or player_1 is None or player_2 is None:
            continue
        for player, opponent, own_score, seat in ((player_1, player_2, score, 1), (player_2, player_1, 1 - score, 2)):
            record = pairs.records.setdefault((player, opponent), Record())
            _add_game(record, own_score, crashed=ResultCode(match.result) in _CRASHES_OF_PLAYER[seat])
    return pairs


def _finished_matches(session: Session, ladder: Ladder) -> list[MatchRecord]:
    query = select(MatchRecord).where(MatchRecord.ladder_id == ladder.id, MatchRecord.status == FINISHED)
    return list(session.scalars(query.options(selectinload(MatchRecord.player_1_version))))


def _score(match: MatchRecord) -> float | None:
    return score_of_player_1(ResultCode(match.result)) if match.result else None


def _players_of(match: MatchRecord) -> tuple[PlayerKey, PlayerKey]:
    """The match's players, in their seats."""
    if match.player_2_computer_id is not None:
        return (_VERSION, match.player_1_version_id), (_COMPUTER, match.player_2_computer_id)
    assert match.player_2_version_id is not None
    return (_VERSION, match.player_1_version_id), (_VERSION, match.player_2_version_id)


def _count(records: dict[PlayerKey, Record], match: MatchRecord, score: float) -> None:
    code = ResultCode(match.result)
    player_1, player_2 = _players_of(match)
    _add_game(records[player_1], score, crashed=code in _CRASHES_OF_PLAYER[1])
    _add_game(records[player_2], 1 - score, crashed=code in _CRASHES_OF_PLAYER[2])


def _add_game(record: Record, score: float, *, crashed: bool) -> None:
    if score == 1:
        record.wins += 1
    elif score == 0:
        record.losses += 1
    else:
        record.ties += 1
    if crashed:
        record.crashes += 1


def rating_of_latest_version(session: Session, ladder: Ladder, entry: LadderEntry) -> Standing | None:
    """The entered bot's standing by its latest version, read alone; `None` if the bot has no version."""
    version = entry.bot.latest_version
    if version is None:
        return None
    rating = session.get(Rating, {"ladder_id": ladder.id, "version_id": version.id})
    return Standing(entry=entry, version=version, rating=rating)
