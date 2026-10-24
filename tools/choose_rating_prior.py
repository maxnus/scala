"""Scores widths of the ratings' prior by how well each predicts a season of results it has not seen.

    uv run python tools/choose_rating_prior.py path/to/matches.csv

The matches are a season of AI Arena's, as the matchmaking prototype (github.com/maxnus/sc2-matchmaking-prototype)
keeps them in `data/matches.csv`, counted as Scala counts results. Taken in time order and cut into slices, each slice
is predicted by the ratings fitted on every game before it. The score is the mean log loss of those predictions, over
all games, and over the games of a bot that had played fewer than 30 before, where the prior weighs most. AI Arena's
bots change between versions, which the season does not tell apart, so every width is scored on the same footing.
"""

import argparse
import csv
from pathlib import Path

import numpy as np

from sc2scala.result_code import ResultCode
from sc2scala.server.bradley_terry import fit_ratings, win_probability
from sc2scala.server.ladder_ratings import score_of_player_1

_PRIOR_WIDTHS = [25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 700, 2000]
_SLICES = 20
_FEW_GAMES = 30
_SMALLEST_PROBABILITY = 1e-6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("matches", type=Path, help="the prototype's data/matches.csv")
    args = parser.parse_args()
    first, second, score, player_count = _rated_games(args.matches)
    print(f"{len(score)} rated games between {player_count} bots, in {_SLICES} slices\n")
    print(f"{'prior (Elo)':>12} {'log loss':>9} {'of bots of few games':>21}")
    for prior_sd in _PRIOR_WIDTHS:
        overall, few_games = _scores(first, second, score, player_count, prior_sd=prior_sd)
        print(f"{prior_sd:>12} {overall:>9.4f} {few_games:>21.4f}")


def _rated_games(matches_file: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    with matches_file.open(encoding="utf-8") as matches:
        rows = [row for row in csv.DictReader(matches) if _score_of(row["result_type"]) is not None]
    rows.sort(key=lambda row: row["result_created"] or row["match_started"])
    bot_ids = sorted({row["bot1_id"] for row in rows} | {row["bot2_id"] for row in rows})
    index_of = {bot_id: index for index, bot_id in enumerate(bot_ids)}
    first = np.array([index_of[row["bot1_id"]] for row in rows])
    second = np.array([index_of[row["bot2_id"]] for row in rows])
    score = np.array([_score_of(row["result_type"]) for row in rows])
    return first, second, score, len(bot_ids)


def _scores(
    first: np.ndarray, second: np.ndarray, score: np.ndarray, player_count: int, *, prior_sd: float
) -> tuple[float, float]:
    """The mean log loss of predicting each slice from the games before it, over all games and over those of a bot
    that had played fewer than `_FEW_GAMES` before."""
    bounds = np.linspace(0, len(score), _SLICES + 1).astype(int)
    losses = []
    few_games_losses = []
    for start, end in zip(bounds[1:-1], bounds[2:], strict=True):
        fitted = fit_ratings(player_count, first[:start], second[:start], score[:start], prior_sd=prior_sd)
        predicted = win_probability(fitted.ratings[first[start:end]] - fitted.ratings[second[start:end]])
        predicted = np.clip(predicted, _SMALLEST_PROBABILITY, 1 - _SMALLEST_PROBABILITY)
        actual = score[start:end]
        loss = -(actual * np.log(predicted) + (1 - actual) * np.log(1 - predicted))
        games_before = np.bincount(first[:start], minlength=player_count) + np.bincount(
            second[:start], minlength=player_count
        )
        few = (games_before[first[start:end]] < _FEW_GAMES) | (games_before[second[start:end]] < _FEW_GAMES)
        losses.append(loss)
        few_games_losses.append(loss[few])
    return float(np.concatenate(losses).mean()), float(np.concatenate(few_games_losses).mean())


def _score_of(result_type: str) -> float | None:
    """Player 1's score as Scala counts it; `None` for a result Scala does not count, or AI Arena's alone."""
    try:
        return score_of_player_1(ResultCode(result_type))
    except ValueError:
        return None


if __name__ == "__main__":
    main()
